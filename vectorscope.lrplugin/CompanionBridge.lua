--[[
  CompanionBridge.lua – Launch and communicate with the Python companion app.

  Architecture (file-based IPC)
  ─────────────────────────────
  1.  The plugin exports a JPEG preview and writes its path to a "trigger"
      file (see ExportPreview.lua).
  2.  This module checks whether the companion process is already running by
      looking for a PID file it writes on start-up.
  3.  If the companion is not running it starts it via os.execute / LrShell.
  4.  The companion app's file-watcher loop sees the updated trigger file and
      re-renders the vectorscope automatically.

  Cross-platform notes
  ─────────────────────
  • Windows: `pythonw` is used so no console window appears.
  • macOS/Linux: `python3` is used with a background `&`.

  The module attempts to locate the companion script relative to the plugin
  directory (../companion/vectorscope_companion.py).
--]]

local LrPathUtils  = import 'LrPathUtils'
local LrFileUtils  = import 'LrFileUtils'
local LrDialogs    = import 'LrDialogs'
local LrLogger     = import 'LrLogger'

local log = LrLogger('VectorscopePlugin')
log:enable('print')

-- ---------------------------------------------------------------------------
-- Module table
-- ---------------------------------------------------------------------------
local CompanionBridge = {}

-- ---------------------------------------------------------------------------
-- Path helpers
-- ---------------------------------------------------------------------------

--- Returns absolute path to the Python companion script.
local function getCompanionScript()
  local pluginDir    = LrPathUtils.parent(_PLUGIN.path)
  return LrPathUtils.child(
    LrPathUtils.child(pluginDir, 'companion'),
    'vectorscope_companion.py'
  )
end

--- Returns path to the PID file the companion writes on start-up.
local function getPidFilePath()
  local pluginDir = LrPathUtils.parent(_PLUGIN.path)
  return LrPathUtils.child(
    LrPathUtils.child(pluginDir, 'vectorscope_tmp'),
    'companion.pid'
  )
end

-- ---------------------------------------------------------------------------
-- Process helpers
-- ---------------------------------------------------------------------------

--- Returns true if the companion process recorded in the PID file is alive.
local function isCompanionRunning()
  local pidFile = getPidFilePath()
  if not LrFileUtils.exists(pidFile) then
    return false
  end

  local f = io.open(pidFile, 'r')
  if not f then return false end
  local pidStr = f:read('*l')
  f:close()

  if not pidStr or pidStr == '' then return false end
  local pid = tonumber(pidStr)
  if not pid then return false end

  -- Check whether the process is still alive.
  --   On POSIX: kill -0 <pid> returns 0 if alive.
  --   On Windows: tasklist /FI "PID eq <pid>" lists the process if alive.
  if WIN_ENV then
    local rc = os.execute('tasklist /FI "PID eq ' .. pid .. '" 2>NUL | find /I "python" >NUL 2>&1')
    return (rc == 0)
  else
    local rc = os.execute('kill -0 ' .. pid .. ' 2>/dev/null')
    return (rc == 0)
  end
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

--- Launch the companion app if it is not already running.
--
-- @param  triggerPath  string  Path to the trigger file the companion watches
-- @return boolean              true if launch succeeded (or already running)
function CompanionBridge.ensureRunning(triggerPath)
  local script = getCompanionScript()
  if not LrFileUtils.exists(script) then
    LrDialogs.message(
      'Vectorscope – Companion not found',
      'Could not find the Python companion app at:\n' .. script ..
      '\n\nPlease ensure the "companion" folder is present next to the plugin.',
      'critical'
    )
    return false
  end

  if isCompanionRunning() then
    log:info('Companion already running.')
    return true
  end

  -- Build the launch command.
  -- We pass the trigger-file path as the first argument so the companion
  -- starts watching the right file immediately.
  local cmd
  if WIN_ENV then
    -- pythonw suppresses the console window on Windows
    cmd = 'start "" pythonw "' .. script .. '" "' .. triggerPath .. '"'
  else
    -- macOS / Linux: launch in background; companion writes its own PID
    cmd = 'python3 "' .. script .. '" "' .. triggerPath .. '" &'
  end

  log:info('Launching companion: ' .. cmd)
  local rc = os.execute(cmd)
  if rc ~= 0 then
    LrDialogs.message(
      'Vectorscope – Launch failed',
      'Failed to start the Python companion app.\n\n' ..
      'Command: ' .. cmd .. '\n\n' ..
      'Make sure Python 3 is installed and the required packages are available.\n' ..
      'See companion/requirements.txt for details.',
      'critical'
    )
    return false
  end

  return true
end

--- Signal the companion app to reload by updating the trigger file.
--
-- This is called after a new JPEG preview has been exported.
-- If the companion is already watching the trigger file it will pick up
-- the change automatically; calling ensureRunning first guarantees it is alive.
--
-- @param  previewPath  string  Absolute path to the new preview JPEG
-- @param  triggerPath  string  Absolute path to the IPC trigger file
function CompanionBridge.signalReload(previewPath, triggerPath)
  local f = io.open(triggerPath, 'w')
  if f then
    -- Write timestamp + path so the file's content changes every time
    f:write(os.time() .. '\n' .. previewPath .. '\n')
    f:close()
    log:info('Trigger file updated: ' .. triggerPath)
  else
    log:warn('Could not write trigger file: ' .. triggerPath)
  end
end

return CompanionBridge
