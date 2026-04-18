--[[
  VectorscopeMain.lua – Main entry point for the Vectorscope Skin Tone Analyzer
  Lightroom Classic plugin.

  This script is invoked when the user selects:
    • File > Plug-in Extras > Open Vectorscope Analyzer  (Library module)
    • File > Plug-in Extras > Open Vectorscope Analyzer  (Develop module)

  Workflow
  ────────
  1.  Check that a photo is currently selected.
  2.  Present a small floating control dialog (non-blocking).
  3.  In a background task:
      a.  Export a downsampled JPEG preview via ExportPreview.
      b.  Ensure the Python companion app is running via CompanionBridge.
      c.  Signal the companion to reload the new preview.
  4.  The control dialog offers an "Update" button the user can press when they
      select a new image, so they can refresh the vectorscope on demand.
--]]

local LrApplication    = import 'LrApplication'
local LrBinding        = import 'LrBinding'
local LrColor          = import 'LrColor'
local LrDate           = import 'LrDate'
local LrDialogs        = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrTasks          = import 'LrTasks'
local LrView           = import 'LrView'
local LrLogger         = import 'LrLogger'

local ExportPreview  = require 'ExportPreview'
local CompanionBridge = require 'CompanionBridge'

local log = LrLogger('VectorscopePlugin')
log:enable('print')

-- ---------------------------------------------------------------------------
-- State shared between UI and background task
-- ---------------------------------------------------------------------------
local state = {
  status             = 'Ready.',                      -- Status line shown in the dialog
  isUpdating         = false,                         -- Prevents concurrent exports
  liveEnabled        = true,                          -- Live mode default
  liveLoopGeneration = 0,                             -- Cancels stale loops
  liveButtonTitle    = 'Stop Live',
}

-- ---------------------------------------------------------------------------
-- Live-mode constants
-- ---------------------------------------------------------------------------

local LIVE_POLL_INTERVAL_SEC      = 0.35
local LIVE_DEBOUNCE_SEC           = 0.35
local LIVE_MIN_EXPORT_INTERVAL_SEC = 1.0

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

local function setStatus(props, text)
  state.status = text
  if props then
    props.status = text
  end
end

local function setLiveEnabled(props, enabled)
  state.liveEnabled = enabled
  state.liveButtonTitle = enabled and 'Stop Live' or 'Start Live'
  if props then
    props.liveButtonTitle = state.liveButtonTitle
  end
end

local function getPhotoFingerprint()
  local catalog = LrApplication.activeCatalog()
  local photo   = catalog:getTargetPhoto()
  if not photo then
    return nil
  end

  local parts = {
    tostring(photo:getRawMetadata('path') or ''),
    tostring(photo:getRawMetadata('fileName') or ''),
  }

  local ok, settings = pcall(function()
    return photo:getDevelopSettings()
  end)
  if ok and settings then
    local keys = {
      'Exposure2012', 'Contrast2012', 'Highlights2012', 'Shadows2012',
      'Whites2012', 'Blacks2012', 'Temp', 'Tint', 'Vibrance', 'Saturation',
      'Clarity2012', 'Dehaze', 'Sharpness', 'LuminanceSmoothing',
      'SplitToningBalance', 'ColorNoiseReduction',
    }
    for _, key in ipairs(keys) do
      parts[#parts + 1] = key .. '=' .. tostring(settings[key])
    end
  end

  return table.concat(parts, '|')
end

-- ---------------------------------------------------------------------------
-- Background export + launch task
-- ---------------------------------------------------------------------------

--- Export the current photo and (re)launch / signal the companion.
-- Runs inside LrTasks.startAsyncTask so Lightroom does not block.
local function runAnalysis(opts)
  opts = opts or {}
  if state.isUpdating then return end
  state.isUpdating = true
  local showProgress = opts.showProgress ~= false
  local statusPrefix = opts.statusPrefix or 'Updating vectorscope'
  local props = opts.props
  setStatus(props, statusPrefix .. '…')

  LrFunctionContext.callWithContext('VectorscopeExport', function(context)
    local progress = nil
    if showProgress then
      progress = LrDialogs.showModalProgressDialog({
        title   = 'Vectorscope – Exporting Preview',
        caption = 'Preparing image for analysis…',
        width   = 300,
        cannotCancel = false,
        functionContext = context,
      })
    end

    -- Export JPEG preview
    local previewPath = ExportPreview.exportCurrentPhoto(progress, {
      silentNoPhoto = opts.silentNoPhoto == true,
    })
    if not previewPath then
      if opts.noPhotoStatus then
        setStatus(props, opts.noPhotoStatus)
      else
        setStatus(props, 'Export failed. Please select a photo and try again.')
      end
      state.isUpdating = false
      return
    end

    if progress then
      progress:setCaption('Launching companion app…')
    end

    -- Ensure companion is running, then signal it
    local triggerPath = ExportPreview.getTriggerPath()
    local ok = CompanionBridge.ensureRunning(triggerPath)
    if ok then
      CompanionBridge.signalReload(previewPath, triggerPath)
      setStatus(props, statusPrefix .. ' complete.')
    else
      setStatus(props, 'Could not start companion app.')
    end

    state.isUpdating = false
  end)
end

-- ---------------------------------------------------------------------------
-- Control dialog (floating, non-blocking)
-- ---------------------------------------------------------------------------

--- Build and show the lightweight control dialog.
local function showControlDialog()
  LrFunctionContext.callWithContext('VectorscopeDialog', function(context)
    local f        = LrView.osFactory()
    local props    = LrBinding.makePropertyTable(context)
    props.status   = state.status
    props.liveButtonTitle = state.liveButtonTitle

    local function startLiveLoop()
      if not state.liveEnabled then
        return
      end

      state.liveLoopGeneration = state.liveLoopGeneration + 1
      local generation = state.liveLoopGeneration
      setStatus(props, 'Live mode enabled. Watching Lightroom changes…')

      LrTasks.startAsyncTask(function()
        local lastFingerprint = nil
        local pendingSince    = nil
        local lastExportAt    = 0

        while state.liveEnabled and generation == state.liveLoopGeneration do
          local now = LrDate.currentTime()
          local fingerprint = getPhotoFingerprint()

          if fingerprint ~= lastFingerprint then
            lastFingerprint = fingerprint
            pendingSince    = now
            if not fingerprint then
              setStatus(props, 'Live mode: no selected photo.')
            else
              setStatus(props, 'Live mode: change detected, waiting for settle…')
            end
          end

          if fingerprint and pendingSince
             and (now - pendingSince) >= LIVE_DEBOUNCE_SEC
             and (now - lastExportAt) >= LIVE_MIN_EXPORT_INTERVAL_SEC then
            runAnalysis({
              showProgress = false,
              statusPrefix = 'Live update',
              props = props,
              silentNoPhoto = true,
              noPhotoStatus = 'Live mode: no selected photo.',
            })
            lastExportAt = LrDate.currentTime()
            pendingSince = nil
          end

          LrTasks.sleep(LIVE_POLL_INTERVAL_SEC)
        end

        if generation == state.liveLoopGeneration then
          setStatus(props, 'Live mode stopped.')
        end
      end)
    end

    local function stopLiveLoop()
      setLiveEnabled(props, false)
      state.liveLoopGeneration = state.liveLoopGeneration + 1
      setStatus(props, 'Live mode stopped. Use "Refresh Now" or restart live mode.')
    end

    -- Layout
    local contents = f:column({
      spacing = f:control_spacing(),
      bind_to_object = props,

      -- Title
      f:static_text({
        title      = 'Vectorscope Skin Tone Analyzer',
        font       = '<system/bold>',
        fill_horizontal = 1,
      }),

      f:separator({ fill_horizontal = 1 }),

      -- Instructions
      f:static_text({
        title = 'Auto Live mode is enabled by default.\n'
              .. 'Adjust the selected photo in Lightroom and\n'
              .. 'the companion vectorscope updates automatically.',
        height_in_lines = 3,
        fill_horizontal = 1,
      }),

      f:separator({ fill_horizontal = 1 }),

      -- Status line
      f:static_text({
        title           = LrView.bind('status'),
        text_color      = LrColor(0.3, 0.6, 1.0),
        fill_horizontal = 1,
        height_in_lines = 2,
      }),

      -- Buttons
      f:row({
        spacing = f:label_spacing(),
        fill_horizontal = 1,

        f:push_button({
          title = LrView.bind('liveButtonTitle'),
          action = function()
            if state.liveEnabled then
              stopLiveLoop()
            else
              setLiveEnabled(props, true)
              startLiveLoop()
            end
          end,
        }),

        f:push_button({
          title  = 'Refresh Now',
          action = function()
            LrTasks.startAsyncTask(function()
              runAnalysis({
                showProgress = true,
                statusPrefix = 'Manual refresh',
                props = props,
                silentNoPhoto = false,
              })
            end)
          end,
        }),

        f:push_button({
          title  = 'Close',
          action = function()
            stopLiveLoop()
            LrDialogs.stopModalWithResult(contents, 'ok')
          end,
        }),
      }),
    })

    -- Show as a floating modal dialog.
    -- The user can leave it open and press "Analyze" whenever they switch images.
    LrDialogs.presentModalDialog({
      title    = 'Vectorscope Skin Tone Analyzer',
      contents = contents,
      resizable = false,
    })

    stopLiveLoop()
  end)
end

-- ---------------------------------------------------------------------------
-- Entry point – called by Lightroom when the menu item is chosen
-- ---------------------------------------------------------------------------
showControlDialog()
