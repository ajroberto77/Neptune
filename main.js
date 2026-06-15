// main.js — Electron main process for Neptune.
//
// Forked from the Iridium Backend / Mercury shell: a GUI host that supervises the Python
// FastAPI backend and drives it over IPC. Responsibilities:
//   1. Create the application window (dev: Vite @ :5173, prod: built frontend/dist/).
//   2. Spawn and supervise the Python FastAPI backend (scripts/neptune_api.py on :8433),
//      injecting the database URLs + provider keys from saved settings.
//   3. Expose IPC for the renderer (via preload.cjs → window.neptune):
//        - config get/save (local bootstrap config in userData)
//        - database connection test
//        - external links, file pickers
//   4. Broadcast the current config to the renderer as window._lastCfg on load and after save.
//
// Neptune owns the *portfolio* database (read-write) and reads securities/macro read-only —
// those are written by Iridium Backend. The default API port is 8433 so Neptune and Iridium
// Backend (8432) can run side-by-side.

import { app, BrowserWindow, ipcMain, shell, dialog } from 'electron';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

const isDev      = process.env.NODE_ENV === 'development';
const API_SCRIPT = path.join(__dirname, 'scripts', 'neptune_api.py');

let mainWindow = null;
let apiProcess = null;
// When true, the backend is (re)launched on a throwaway SQLite DB with seeded demo data instead
// of the configured Postgres — the "Test Mode" escape hatch when no real database is reachable.
let testMode = false;

// ── Config (local bootstrap; richer settings may later live in a settings DB) ──

const CONFIG_PATH = () => path.join(app.getPath('userData'), 'neptune-config.json');

// Locate the project's own virtual environment, if one exists, next to the app. Windows puts
// the interpreter in venv\Scripts\python.exe; POSIX in venv/bin/python. Returns the absolute
// path or null. (Matches the Iridium/Mercury convention — ship a venv, not a frozen binary.)
function findVenvPython() {
  const candidates = process.platform === 'win32'
    ? [path.join(__dirname, 'venv', 'Scripts', 'python.exe'),
       path.join(__dirname, '.venv', 'Scripts', 'python.exe')]
    : [path.join(__dirname, 'venv', 'bin', 'python3'),
       path.join(__dirname, 'venv', 'bin', 'python'),
       path.join(__dirname, '.venv', 'bin', 'python3'),
       path.join(__dirname, '.venv', 'bin', 'python')];
  for (const c of candidates) {
    try { if (fs.existsSync(c)) return c; } catch { /* ignore */ }
  }
  return null;
}

const BARE_DEFAULTS = new Set(['', 'python', 'python3', 'python.exe']);

function resolvePython(cfg) {
  const set = (cfg && cfg.python && cfg.python.executable || '').trim();
  if (set && !BARE_DEFAULTS.has(set)) return set;            // explicit override
  return findVenvPython() || (process.platform === 'win32' ? 'python' : 'python3');
}

function defaultConfig() {
  return {
    // Neptune's own database (read-write) — the only DB Neptune writes.
    portfolioDb: {
      host: 'localhost', port: 5432, database: 'neptune_portfolios',
      user: 'postgres', password: '',
    },
    // Read-only market-data database (written by Iridium Backend).
    securitiesDb: {
      host: 'localhost', port: 5432, database: 'neptune_securities',
      user: 'postgres', password: '',
    },
    // Read-only macro database (written by Iridium Backend).
    macroDb: {
      host: 'localhost', port: 5432, database: 'neptune_macro',
      user: 'postgres', password: '',
    },
    // Read-only cato_securities universe master. database:'' means "not configured".
    universeDb: {
      host: 'localhost', port: 5432, database: '',
      user: 'postgres', password: '',
    },
    api: { host: '127.0.0.1', port: 8433 },
    providers: { fredApiKey: '' },
    python: {
      executable: findVenvPython() || (process.platform === 'win32' ? 'python' : 'python3'),
    },
  };
}

function loadConfig() {
  try {
    const saved = JSON.parse(fs.readFileSync(CONFIG_PATH(), 'utf-8'));
    const d = defaultConfig();
    return {
      ...d, ...saved,
      portfolioDb:  { ...d.portfolioDb,  ...(saved.portfolioDb  || {}) },
      securitiesDb: { ...d.securitiesDb, ...(saved.securitiesDb || {}) },
      macroDb:      { ...d.macroDb,      ...(saved.macroDb      || {}) },
      universeDb:   { ...d.universeDb,   ...(saved.universeDb   || {}) },
      api:          { ...d.api,          ...(saved.api          || {}) },
      providers:    { ...d.providers,    ...(saved.providers    || {}) },
      python:       { ...d.python,       ...(saved.python       || {}) },
    };
  } catch {
    return defaultConfig();
  }
}

function saveConfig(cfg) {
  fs.mkdirSync(path.dirname(CONFIG_PATH()), { recursive: true });
  fs.writeFileSync(CONFIG_PATH(), JSON.stringify(cfg, null, 2), 'utf-8');
}

// SQLAlchemy URL with the psycopg (v3) driver, matching requirements.txt.
function buildDbUrl(db) {
  const enc = encodeURIComponent;
  const auth = `${enc(db.user || '')}:${enc(db.password || '')}`;
  return `postgresql+psycopg://${auth}@${db.host}:${db.port}/${db.database}`;
}

function apiBaseUrl(cfg) {
  return `http://${cfg.api.host}:${cfg.api.port}`;
}

// The env the Python backend runs with: the database URLs + provider keys built from saved
// settings. Optional URLs (universe) and the FRED key are only set when configured, so the
// Python side cleanly reports "not configured" rather than seeing a junk value.
//
// Test Mode is the escape hatch when no real database is reachable: it forces the backend onto a
// local SQLite file for EVERY role (DATABASE_URL is the per-role fallback), seeds the golden demo
// book (NEPTUNE_SEED_DEMO_POSITIONS), and lets the synthetic market-data fallback kick in — so the
// app is fully explorable with throwaway data and zero Postgres. It never touches real DBs.
function backendEnv(cfg) {
  if (testMode) {
    const dbFile = path.join(app.getPath('userData'), 'neptune-test.db').replace(/\\/g, '/');
    const env = {
      ...process.env,
      DATABASE_URL: `sqlite+pysqlite:///${dbFile}`,
      NEPTUNE_SEED_DEMO_POSITIONS: '1',
      NEPTUNE_API_HOST: cfg.api.host,
      NEPTUNE_API_PORT: String(cfg.api.port),
    };
    // Make sure no real Postgres role URL leaks in from the parent environment.
    delete env.PORTFOLIO_DATABASE_URL;
    delete env.SECURITIES_DATABASE_URL;
    delete env.MACRO_DATABASE_URL;
    delete env.UNIVERSE_DATABASE_URL;
    return env;
  }

  const env = {
    ...process.env,
    PORTFOLIO_DATABASE_URL:  buildDbUrl(cfg.portfolioDb),
    SECURITIES_DATABASE_URL: buildDbUrl(cfg.securitiesDb),
    MACRO_DATABASE_URL:      buildDbUrl(cfg.macroDb),
    NEPTUNE_API_HOST:        cfg.api.host,
    NEPTUNE_API_PORT:        String(cfg.api.port),
  };
  if (cfg.universeDb && cfg.universeDb.database) {
    env.UNIVERSE_DATABASE_URL = buildDbUrl(cfg.universeDb);
  }
  if (cfg.providers && cfg.providers.fredApiKey) {
    env.FRED_API_KEY = cfg.providers.fredApiKey;
  }
  return env;
}

// ── Python FastAPI backend lifecycle ──────────────────────────────────────────

function startApi(cfg) {
  stopApi();
  const py = resolvePython(cfg);
  console.log(`[neptune] starting API: ${py} ${API_SCRIPT}`);
  apiProcess = spawn(py, [API_SCRIPT], { cwd: __dirname, env: backendEnv(cfg) });

  const relay = (stream) => {
    stream.setEncoding('utf-8');
    stream.on('data', (chunk) => {
      process.stdout.write(`[api] ${chunk}`);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('api:log', chunk.toString());
      }
    });
  };
  relay(apiProcess.stdout);
  relay(apiProcess.stderr);

  apiProcess.on('exit', (code) => {
    console.log(`[neptune] API exited with code ${code}`);
    apiProcess = null;
  });
  apiProcess.on('error', (err) => {
    console.error(`[neptune] failed to start API: ${err.message}`);
  });
}

function stopApi() {
  if (apiProcess) {
    apiProcess.kill();
    apiProcess = null;
  }
}

// ── Window ────────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    backgroundColor: '#0e1b2a',
    // Frameless: the app draws its own TitleBar (with window controls) like the rest of the
    // Iridium suite. `-webkit-app-region: drag` on that bar moves the window.
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    if (process.env.NEPTUNE_DEVTOOLS === '1') {
      mainWindow.webContents.openDevTools();
    }
  } else {
    mainWindow.loadFile(path.join(__dirname, 'frontend', 'dist', 'index.html'));
  }

  mainWindow.webContents.on('did-finish-load', () => {
    broadcastConfig(loadConfig());
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// Inject window._lastCfg into the renderer (also emits a 'config:changed' event via preload).
function broadcastConfig(cfg) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const payload = JSON.stringify(cfg);
  mainWindow.webContents.executeJavaScript(`window._lastCfg = ${payload};`).catch(() => {});
  mainWindow.webContents.send('config:changed', cfg);
}

// ── IPC handlers (renderer ↔ main) ────────────────────────────────────────────

function registerIpc() {
  ipcMain.handle('config:get', () => loadConfig());

  ipcMain.handle('config:save', (_evt, cfg) => {
    saveConfig(cfg);
    broadcastConfig(cfg);
    startApi(cfg);            // reconnect backend to the (possibly new) databases
    return { ok: true };
  });

  ipcMain.handle('app:getApiBaseUrl', () => apiBaseUrl(loadConfig()));

  // Test Mode: relaunch the backend on throwaway SQLite (+ seeded demo book) or back on the
  // configured Postgres. Returns the resulting mode so the renderer can reflect it.
  ipcMain.handle('app:isTestMode', () => testMode);
  ipcMain.handle('app:setTestMode', (_evt, on) => {
    testMode = Boolean(on);
    startApi(loadConfig());
    return testMode;
  });

  // Test a database connection without saving it. Spawns a short-lived python that opens and
  // closes a connection using the provided settings.
  ipcMain.handle('db:test', async (_evt, db) => {
    const cfg = loadConfig();
    const script =
      "import os,sqlalchemy as sa;" +
      "e=sa.create_engine(os.environ['TEST_DB_URL']);" +
      "c=e.connect(); c.close(); print('OK')";
    return await new Promise((resolve) => {
      const env = { ...process.env, TEST_DB_URL: buildDbUrl(db) };
      const p = spawn(resolvePython(cfg), ['-c', script], { cwd: __dirname, env });
      let out = '', err = '';
      p.stdout.on('data', (d) => (out += d));
      p.stderr.on('data', (d) => (err += d));
      p.on('error', (e) => resolve({ ok: false, message: e.message }));
      p.on('close', (code) => {
        if (code === 0 && out.includes('OK')) resolve({ ok: true, message: 'Connection OK' });
        else resolve({ ok: false, message: (err || out || `exit ${code}`).trim() });
      });
    });
  });

  // Window controls for the frameless TitleBar (the OS chrome is gone, so the app drives these).
  ipcMain.handle('win:minimize', () => mainWindow?.minimize());
  ipcMain.handle('win:toggleMaximize', () => {
    if (!mainWindow) return false;
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
      return false;
    }
    mainWindow.maximize();
    return true;
  });
  ipcMain.handle('win:close', () => mainWindow?.close());

  ipcMain.handle('shell:openExternal', (_evt, url) => shell.openExternal(url));

  ipcMain.handle('dialog:openFile', async (_evt, options) => {
    const res = await dialog.showOpenDialog(mainWindow, options || { properties: ['openFile'] });
    return res.canceled ? null : res.filePaths[0];
  });
}

// ── App lifecycle ──────────────────────────────────────────────────────────────

app.on('ready', () => {
  registerIpc();
  startApi(loadConfig());
  createWindow();
});

app.on('window-all-closed', () => {
  stopApi();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', stopApi);

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});
