// preload.cjs — secure bridge between the renderer and the Electron main process.
//
// CommonJS (.cjs) is required because the Electron package.json sets "type": "module", which
// would otherwise treat a .js preload as an ES module (incompatible with the sandboxed preload
// loader). Exposes a single typed surface: window.neptune.

const { contextBridge, ipcRenderer } = require('electron');

// Local cache of the latest config, kept in sync via the 'config:changed' event so the
// renderer can read it synchronously (mirrors window._lastCfg).
let lastConfig = null;
ipcRenderer.on('config:changed', (_evt, cfg) => { lastConfig = cfg; });

contextBridge.exposeInMainWorld('neptune', {
  // ── Config ──────────────────────────────────────────────────────────────
  getConfig:   () => ipcRenderer.invoke('config:get'),
  saveConfig:  (cfg) => ipcRenderer.invoke('config:save', cfg),
  lastConfig:  () => lastConfig,
  onConfigChanged: (cb) => {
    const handler = (_evt, cfg) => cb(cfg);
    ipcRenderer.on('config:changed', handler);
    return () => ipcRenderer.removeListener('config:changed', handler);
  },

  // ── Backend ─────────────────────────────────────────────────────────────
  getApiBaseUrl:   () => ipcRenderer.invoke('app:getApiBaseUrl'),
  testDbConnection: (db) => ipcRenderer.invoke('db:test', db),

  // Test Mode: relaunch the backend on throwaway SQLite (+ seeded demo data), or back on Postgres.
  isTestMode:    () => ipcRenderer.invoke('app:isTestMode'),
  startTestMode: () => ipcRenderer.invoke('app:setTestMode', true),
  stopTestMode:  () => ipcRenderer.invoke('app:setTestMode', false),
  onApiLog: (cb) => {
    const handler = (_evt, line) => cb(line);
    ipcRenderer.on('api:log', handler);
    return () => ipcRenderer.removeListener('api:log', handler);
  },

  // ── Window controls (frameless TitleBar) ─────────────────────────────────
  minimizeWindow:       () => ipcRenderer.invoke('win:minimize'),
  toggleMaximizeWindow: () => ipcRenderer.invoke('win:toggleMaximize'),
  closeWindow:          () => ipcRenderer.invoke('win:close'),

  // ── Misc ────────────────────────────────────────────────────────────────
  openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),
  pickFile:     (options) => ipcRenderer.invoke('dialog:openFile', options),
});
