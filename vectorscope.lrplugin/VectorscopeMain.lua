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
  status      = 'Ready.',   -- Status line shown in the dialog
  isUpdating  = false,      -- Prevents concurrent exports
}

-- ---------------------------------------------------------------------------
-- Background export + launch task
-- ---------------------------------------------------------------------------

--- Export the current photo and (re)launch / signal the companion.
-- Runs inside LrTasks.startAsyncTask so Lightroom does not block.
local function runAnalysis(progressScope)
  if state.isUpdating then return end
  state.isUpdating = true
  state.status     = 'Exporting preview…'

  LrFunctionContext.callWithContext('VectorscopeExport', function(context)
    local progress = LrDialogs.showModalProgressDialog({
      title   = 'Vectorscope – Exporting Preview',
      caption = 'Preparing image for analysis…',
      width   = 300,
      cannotCancel = false,
      functionContext = context,
    })

    -- Export JPEG preview
    local previewPath = ExportPreview.exportCurrentPhoto(progress)
    if not previewPath then
      state.status     = 'Export failed. Please select a photo and try again.'
      state.isUpdating = false
      return
    end

    progress:setCaption('Launching companion app…')

    -- Ensure companion is running, then signal it
    local triggerPath = ExportPreview.getTriggerPath()
    local ok = CompanionBridge.ensureRunning(triggerPath)
    if ok then
      CompanionBridge.signalReload(previewPath, triggerPath)
      state.status = 'Vectorscope updated. See the companion window.'
    else
      state.status = 'Could not start companion app.'
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
        title = 'Select a photo in the Library or Develop module,\n'
              .. 'then press "Analyze" to open the vectorscope\n'
              .. 'in the external companion window.',
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
          title  = 'Analyze Current Photo',
          action = function()
            props.status   = 'Exporting…'
            state.status   = 'Exporting…'
            LrTasks.startAsyncTask(function()
              runAnalysis()
              props.status = state.status
            end)
          end,
        }),

        f:push_button({
          title  = 'Close',
          action = function()
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
  end)
end

-- ---------------------------------------------------------------------------
-- Entry point – called by Lightroom when the menu item is chosen
-- ---------------------------------------------------------------------------
showControlDialog()
