// main.js — Electron main process for Neptune.
//
// Forked from the Iridium Backend / Mercury shell: a GUI host that supervises the Python
// FastAPI backend and drives it over IPC. Responsibilities:
//   1. Create the application window (dev: Vite @ :5176, prod: built frontend/dist/).
//   2. Spawn and supervise the Python FastAPI backend (scripts/neptune_api.py on :8433),
//      injecting the database URLs + provider keys from saved settings.
//   3. Expose IPC for the renderer (via preload.cjs → window.neptune):
//        - config get/save (local bootstrap config in userData)
//        - database connection test
//        - frameless-window controls
//   4. Broadcast the current config to the renderer as window._lastCfg on load and after save.
//   5. After every non-test-mode (re)start, wait for the backend to come up and push the
//      current SECURITIES/MACRO/UNIVERSE config into the portfolio DB's stored connection
//      rows too — those resolve live from the stored row, which wins over env unconditionally,
//      so without this a stale row from an earlier session could silently override what this
//      one just configured (see syncStoredConnections below).
//
// Neptune owns the *portfolio* database (read-write) and reads securities/macro read-only —
// those are written by Iridium Backend. The default API port is 8433 so Neptune and Iridium
// Backend (8432) can run side-by-side.

import { app, BrowserWindow, ipcMain } from 'electron';
import path from 'path';
import fs from 'fs';
import net from 'net';
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
  return findVenvPython() || (process.platform === 'win32' ? 'py' : 'python3');
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

// The in-flight (or most recent) resolution of the backend's actual port — usually
// cfg.api.port, but can differ when that port was already taken (see findFreePort below),
// e.g. a stale sidecar from a prior run still holding it. app:getApiBaseUrl must await this
// rather than reading cfg.api.port directly, since the renderer's first health poll fires
// immediately on window load and could otherwise race ahead of port resolution and cache
// the wrong (configured, not actual) port for the whole session.
let portResolutionPromise = null;

// Probe for a free port starting at startPort, trying up to maxAttempts sequential ports.
// Binds a throwaway server to each candidate to test it, then releases it immediately — a
// small window exists between release and the real backend binding, but that's fine here:
// the failure mode this fixes is a *stale* leftover process, not live contention.
function findFreePort(startPort, host, maxAttempts = 20) {
  return new Promise((resolve, reject) => {
    const tryPort = (port, attemptsLeft) => {
      const tester = net.createServer();
      tester.once('error', (err) => {
        if (err.code === 'EADDRINUSE' && attemptsLeft > 0) {
          tester.close(() => tryPort(port + 1, attemptsLeft - 1));
        } else {
          reject(err);
        }
      });
      tester.once('listening', () => {
        tester.close(() => resolve(port));
      });
      tester.listen(port, host);
    };
    tryPort(startPort, maxAttempts);
  });
}

async function startApi(cfg) {
  stopApi();

  portResolutionPromise = findFreePort(cfg.api.port, cfg.api.host).catch((err) => {
    console.warn(`[neptune] could not find a free port near ${cfg.api.port}: ${err.message}`);
    return cfg.api.port;
  });
  const port = await portResolutionPromise;
  if (port !== cfg.api.port) {
    console.warn(`[neptune] port ${cfg.api.port} is in use; falling back to ${port}`);
  }
  const effectiveCfg = { ...cfg, api: { ...cfg.api, port } };

  const py = resolvePython(cfg);
  console.log(`[neptune] starting API: ${py} ${API_SCRIPT}`);
  apiProcess = spawn(py, [API_SCRIPT], { cwd: __dirname, env: backendEnv(effectiveCfg) });

  const relay = (stream) => {
    stream.setEncoding('utf-8');
    stream.on('data', (chunk) => {
      process.stdout.write(`[api] ${chunk}`);
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

  // Test Mode's throwaway SQLite has no real db_connections table worth protecting.
  if (!testMode) {
    const healthy = await waitForApiHealth(effectiveCfg);
    if (healthy) {
      await syncStoredConnections(effectiveCfg);
    } else {
      console.warn('[neptune] API did not become healthy in time; skipping connection sync');
    }
  }
}

function stopApi() {
  if (apiProcess) {
    apiProcess.kill();
    apiProcess = null;
  }
}

// ── Keeping the stored connection rows in sync ────────────────────────────────
//
// SECURITIES/MACRO/UNIVERSE resolve live from a `db_connections` row stored IN the
// portfolio Postgres DB when one exists — it wins over env UNCONDITIONALLY (see
// settings_store/service.py's resolve_url()). Electron's own source of truth is
// neptune-config.json, injected as env vars on every spawn — but nothing kept the
// stored row in sync with it, so a row left over from an earlier session (the web UI,
// or a prior Electron config) would silently override what THIS session just
// configured, with no error and no UI signal. Fixed by pushing the current config into
// the stored rows after every non-test-mode start, so "stored row wins" is always
// correct-by-construction instead of stale. PORTFOLIO is excluded deliberately — it's
// env-only by necessity, since the table being written lives in the database being
// pointed at (see db/runtime.py's docstring). Best-effort: a failure here never blocks
// the config save or the backend restart, since the backend is already running with the
// right env vars for this process either way — it only protects against a FUTURE boot
// (this session or a later one) silently reverting to a stale stored row.

async function waitForApiHealth(cfg, { timeoutMs = 15000, intervalMs = 300 } = {}) {
  const url = `${apiBaseUrl(cfg)}/health`;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

async function syncStoredConnections(cfg) {
  const roles = [
    ['SECURITIES', cfg.securitiesDb],
    ['MACRO', cfg.macroDb],
    ...(cfg.universeDb && cfg.universeDb.database ? [['UNIVERSE', cfg.universeDb]] : []),
  ];
  const base = apiBaseUrl(cfg);
  for (const [role, db] of roles) {
    try {
      const res = await fetch(`${base}/settings/connections/${role}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          host: db.host,
          port: db.port,
          database: db.database,
          username: db.user,
          // Electron's cfg is the FULL desired state, not a partial edit form — always
          // send the real value (including '') rather than null, which the API treats
          // as "leave the stored secret unchanged" (the web form's semantics, not ours).
          password: db.password ?? '',
        }),
      });
      if (!res.ok) {
        console.warn(`[neptune] sync ${role} connection failed: HTTP ${res.status}`);
      }
    } catch (err) {
      console.warn(`[neptune] sync ${role} connection failed: ${err.message}`);
    }
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
    // Single `npm run dev` starts Vite and Electron together; Electron can win the race, so
    // retry the dev-server URL until it answers instead of showing a failed-load page.
    const DEV_URL = 'http://localhost:5176';
    const loadDev = () => mainWindow && mainWindow.loadURL(DEV_URL).catch(() => {});
    mainWindow.webContents.on('did-fail-load', () => {
      if (mainWindow && !mainWindow.isDestroyed()) setTimeout(loadDev, 500);
    });
    loadDev();
    if (process.env.NEPTUNE_DEVTOOLS === '1') {
      mainWindow.webContents.openDevTools();
    }
  } else {
    mainWindow.loadFile(path.join(__dirname, 'frontend', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── IPC handlers (renderer ↔ main) ────────────────────────────────────────────

function registerIpc() {
  ipcMain.handle('config:get', () => loadConfig());

  ipcMain.handle('config:save', async (_evt, cfg) => {
    saveConfig(cfg);
    await startApi(cfg);      // reconnect backend to the (possibly new) databases, then sync
    return { ok: true };
  });

  ipcMain.handle('app:getApiBaseUrl', async () => {
    const cfg = loadConfig();
    if (portResolutionPromise) {
      const port = await portResolutionPromise.catch(() => cfg.api.port);
      return apiBaseUrl({ ...cfg, api: { ...cfg.api, port } });
    }
    return apiBaseUrl(cfg);
  });

  // Test Mode: relaunch the backend on throwaway SQLite (+ seeded demo book) or back on the
  // configured Postgres. Returns the resulting mode so the renderer can reflect it.
  ipcMain.handle('app:isTestMode', () => testMode);
  ipcMain.handle('app:setTestMode', async (_evt, on) => {
    testMode = Boolean(on);
    await startApi(loadConfig());
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
