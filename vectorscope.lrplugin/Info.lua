--[[
  Info.lua – Lightroom plugin manifest for the Vectorscope Skin Tone Analyzer.

  This file is required by the Lightroom SDK.  It declares the minimum SDK
  version, the plugin identifier, the human-readable plugin name, and the
  entry points (menu items) that Lightroom will expose to the user.
--]]

return {
  -- Minimum Lightroom SDK version required (Classic 6 / CC 2015 and above)
  LrSdkVersion        = 6.0,
  LrSdkMinimumVersion = 5.0,

  -- Unique reverse-DNS identifier for this plugin
  LrToolkitIdentifier = 'com.deotime.vectorscope',

  -- Human-readable name shown in the Plug-in Manager
  LrPluginName = LOC '$$$/VectorscopePlugin/PluginName=Vectorscope Skin Tone Analyzer',

  -- Link opened by "More Info …" in the Plug-in Manager
  LrPluginInfoUrl = 'https://github.com/DeoTime/vectorscope',

  -- ---------------------------------------------------------------------------
  -- Library-module menu items  (File > Plug-in Extras when in Library)
  -- ---------------------------------------------------------------------------
  LrLibraryMenuItems = {
    {
      title = LOC '$$$/VectorscopePlugin/Menu/OpenVectorscope=Open Vectorscope Analyzer',
      file  = 'VectorscopeMain.lua',
    },
  },

  -- ---------------------------------------------------------------------------
  -- Develop-module menu items  (File > Plug-in Extras when in Develop)
  -- ---------------------------------------------------------------------------
  LrDevelopMenuItems = {
    {
      title = LOC '$$$/VectorscopePlugin/Menu/OpenVectorscope=Open Vectorscope Analyzer',
      file  = 'VectorscopeMain.lua',
    },
  },

  -- ---------------------------------------------------------------------------
  -- Plugin version
  -- ---------------------------------------------------------------------------
  VERSION = { major = 1, minor = 0, revision = 0, build = 1 },
}
