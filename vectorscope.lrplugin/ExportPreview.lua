--[[
  ExportPreview.lua – Utility module for exporting a JPEG preview of the
  currently selected Lightroom photo to a temporary directory.

  The companion Python app reads this JPEG to render the vectorscope.
  We export at a capped resolution (1024 px on the long edge) so that the
  companion app can process the image in near real-time even for large RAWs.

  Usage (from another module):
    local ExportPreview = require 'ExportPreview'
    local jpegPath = ExportPreview.exportCurrentPhoto(progressScope)
--]]

local LrApplication    = import 'LrApplication'
local LrDialogs        = import 'LrDialogs'
local LrExportSession  = import 'LrExportSession'
local LrPathUtils      = import 'LrPathUtils'
local LrFileUtils      = import 'LrFileUtils'
local LrTasks          = import 'LrTasks'
local LrLogger         = import 'LrLogger'

local log = LrLogger('VectorscopePlugin')
log:enable('print')

-- ---------------------------------------------------------------------------
-- Module table
-- ---------------------------------------------------------------------------
local ExportPreview = {}

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

--- Returns the directory used for temporary preview files.
-- Creates it if it does not yet exist.
local function getTmpDir()
  -- Place temp files next to the plugin so they are easy to find and clean up.
  local pluginDir = LrPathUtils.parent(_PLUGIN.path)
  local tmpDir    = LrPathUtils.child(pluginDir, 'vectorscope_tmp')
  if not LrFileUtils.exists(tmpDir) then
    LrFileUtils.createAllDirectories(tmpDir)
  end
  return tmpDir
end

--- Returns the canonical path for the exported preview JPEG.
function ExportPreview.getPreviewPath()
  return LrPathUtils.child(getTmpDir(), 'vectorscope_preview.jpg')
end

--- Returns the canonical path for the IPC trigger file.
-- The companion app watches this file; writing to it signals that a new
-- preview is ready.
function ExportPreview.getTriggerPath()
  return LrPathUtils.child(getTmpDir(), 'vectorscope_trigger.txt')
end

-- ---------------------------------------------------------------------------
-- Main export function
-- ---------------------------------------------------------------------------

--- Export the currently selected photo as a JPEG preview.
--
-- @param  progressScope  (optional) LrProgressScope to update
-- @return string | nil   Absolute path to the exported JPEG, or nil on error
function ExportPreview.exportCurrentPhoto(progressScope)
  local catalog    = LrApplication.activeCatalog()
  local photo      = catalog:getTargetPhoto()

  if not photo then
    LrDialogs.message(
      'Vectorscope',
      'Please select a photo in the Library or Develop module first.',
      'info'
    )
    return nil
  end

  local previewPath = ExportPreview.getPreviewPath()
  local tmpDir      = getTmpDir()

  -- ------------------------------------------------------------------
  -- Build export settings
  -- ------------------------------------------------------------------
  -- We downsample to a maximum of 1024 px on the long edge.
  -- This is enough for accurate vectorscope analysis while keeping
  -- processing fast (≈3 × 3 = 9 million px at full resolution would
  -- be slow; 1024 × 768 ≈ 786 k px is more than sufficient).
  local exportSettings = {
    -- Destination
    LR_export_destinationType      = 'specificFolder',
    LR_export_destinationPathPrefix = tmpDir,
    LR_export_useSubfolder         = false,
    LR_export_copyMove             = 'nothing',

    -- File settings
    LR_format                      = 'JPEG',
    LR_jpeg_quality                = 0.85,
    LR_export_colorSpace           = 'sRGB',

    -- Output sharpening (off – we want colour data, not edge sharpening)
    LR_outputSharpeningOn          = false,

    -- Resize: long edge = 1024 px
    LR_size_doConstrain            = true,
    LR_size_maxHeight              = 1024,
    LR_size_maxWidth               = 1024,
    LR_size_resizeType             = 'longEdge',
    LR_size_units                  = 'pixels',
    LR_size_doNotEnlarge           = true,

    -- Filename: fixed name so the companion always reads the same file
    LR_tokens                      = 'vectorscope_preview',
    LR_tokenCustomString           = '',
    LR_renamingTokensOn            = true,

    -- Metadata (strip personal data from the preview)
    LR_embeddedMetadataOption      = 'copyrightOnly',
    LR_removeLocationMetadata      = true,
  }

  if progressScope then
    progressScope:setCaption('Exporting preview …')
  end

  -- ------------------------------------------------------------------
  -- Run export session
  -- ------------------------------------------------------------------
  local exportSession = LrExportSession({
    photosToExport = { photo },
    exportSettings = exportSettings,
  })

  local success = true
  exportSession:doExportOnCurrentTask(function(_, rendition)
    if rendition.wasSkipped then
      log:warn('Export was skipped for photo: ' .. tostring(photo:getFormattedMetadata('fileName')))
      success = false
    elseif not rendition.success then
      log:error('Export failed: ' .. tostring(rendition.errorMessage))
      success = false
    end
  end)

  if not success then
    return nil
  end

  -- ------------------------------------------------------------------
  -- Write trigger file so the companion app knows to reload
  -- ------------------------------------------------------------------
  local triggerPath = ExportPreview.getTriggerPath()
  local f = io.open(triggerPath, 'w')
  if f then
    f:write(previewPath .. '\n')
    f:close()
  end

  return previewPath
end

return ExportPreview
