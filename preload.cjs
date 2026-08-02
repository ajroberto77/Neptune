// preload.cjs — secure bridge between the renderer and the Electron main process.
//
// CommonJS (.cjs) is required because the Electron package.json sets "type": "module", which
// would otherwise treat a .js preload as an ES module (incompatible with the sandboxed preload
// loader). Exposes a single typed surface: window.neptune.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('neptune', {
  // ── Config ──────────────────────────────────────────────────────────────
  getConfig:   () => ipcRenderer.invoke('config:get'),
  saveConfig:  (cfg) => ipcRenderer.invoke('config:save', cfg),

  // ── Backend ─────────────────────────────────────────────────────────────
  getApiBaseUrl:   () => ipcRenderer.invoke('app:getApiBaseUrl'),
  testDbConnection: (db) => ipcRenderer.invoke('db:test', db),

  // Test Mode: relaunch the backend on throwaway SQLite (+ seeded demo data), or back on Postgres.
  isTestMode:    () => ipcRenderer.invoke('app:isTestMode'),
  startTestMode: () => ipcRenderer.invoke('app:setTestMode', true),
  stopTestMode:  () => ipcRenderer.invoke('app:setTestMode', false),

  // ── Window controls (frameless TitleBar) ─────────────────────────────────
  minimizeWindow:       () => ipcRenderer.invoke('win:minimize'),
  toggleMaximizeWindow: () => ipcRenderer.invoke('win:toggleMaximize'),
  closeWindow:          () => ipcRenderer.invoke('win:close'),
});
