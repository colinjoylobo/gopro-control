const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn, execSync } = require('child_process');
const http = require('http');

const isWin = process.platform === 'win32';

let mainWindow;
let backendProcess = null;

// Log file for debugging
const logFile = path.join(os.tmpdir(), 'gopro-control.log');
function log(msg) {
  const line = `${new Date().toISOString()} ${msg}\n`;
  try { fs.appendFileSync(logFile, line); } catch (e) {}
  try { console.log(msg); } catch (e) {}
}

// Kill any existing process on port 8000
function killExistingBackend() {
  try {
    if (isWin) {
      const output = execSync('netstat -ano | findstr :8000 | findstr LISTENING', { encoding: 'utf-8' }).trim();
      if (output) {
        const pids = new Set();
        for (const line of output.split('\n')) {
          const parts = line.trim().split(/\s+/);
          const pid = parts[parts.length - 1];
          if (pid && pid !== '0') pids.add(pid);
        }
        for (const pid of pids) {
          log(`Killing PID ${pid} on port 8000`);
          try { execSync(`taskkill /F /T /PID ${pid}`, { encoding: 'utf-8' }); } catch (e) {}
        }
      }
    } else {
      const pids = execSync('lsof -ti:8000 2>/dev/null', { encoding: 'utf-8' }).trim();
      if (pids) {
        log(`Killing existing processes on port 8000: ${pids}`);
        execSync(`kill -9 ${pids.split('\n').join(' ')} 2>/dev/null`);
      }
    }
  } catch (e) {
    // No process on port 8000, that's fine
  }
}

// Find backend directory — tries multiple locations
function findBackendDir() {
  const candidates = [];
  const exeName = isWin ? 'gopro-backend.exe' : 'gopro-backend';

  if (app.isPackaged) {
    // Production: check Resources folder for bundled backend
    candidates.push(path.join(process.resourcesPath, 'backend', 'dist', 'gopro-backend'));
    // Also check relative to app bundle
    const exeDir = path.dirname(process.execPath);
    if (isWin) {
      // Windows: exe is in the install directory, backend is alongside
      candidates.push(path.join(exeDir, 'resources', 'backend', 'dist', 'gopro-backend'));
      candidates.push(path.resolve(exeDir, '..', 'backend'));
    } else {
      // macOS: relative to .app bundle
      candidates.push(path.resolve(exeDir, '..', '..', '..', '..', '..', '..', 'backend'));
    }
  } else {
    // Development: relative to electron/main.js
    candidates.push(path.join(__dirname, '..', '..', 'backend'));
  }

  // Known project locations fallback
  candidates.push(path.join(os.homedir(), 'G5', 'Hanuman', 'gopro-control-v2', 'backend'));
  candidates.push(path.join(os.homedir(), 'G5', 'Hanuman', 'gopro-control', 'backend'));

  log(`Backend candidates: ${JSON.stringify(candidates)}`);

  for (const dir of candidates) {
    const mainPy = path.join(dir, 'main.py');
    const exists = fs.existsSync(mainPy);
    log(`  Checking ${dir}/main.py -> ${exists}`);
    if (exists) {
      log(`Found backend at: ${dir}`);
      return dir;
    }
  }

  // Check for bundled executable
  for (const dir of candidates) {
    const exe = path.join(dir, exeName);
    if (fs.existsSync(exe)) {
      log(`Found bundled backend at: ${dir}`);
      return dir;
    }
  }

  log('Backend not found in any candidate!');
  return candidates[0];
}

// Start FastAPI backend
async function startBackend() {
  killExistingBackend();

  const backendDir = findBackendDir();
  const exeName = isWin ? 'gopro-backend.exe' : 'gopro-backend';
  let backendExecutable;
  let backendArgs = [];
  let cwd = backendDir;

  // Check for bundled executable first
  const bundledExe = path.join(backendDir, exeName);
  if (fs.existsSync(bundledExe)) {
    log('Using bundled backend executable');
    backendExecutable = bundledExe;
  } else {
    // Use venv Python
    log('Using Python venv backend');
    const venvPython = isWin
      ? path.join(backendDir, 'venv', 'Scripts', 'python.exe')
      : path.join(backendDir, 'venv', 'bin', 'python3');

    if (fs.existsSync(venvPython)) {
      backendExecutable = venvPython;
    } else {
      // Try alternate venv python name
      const venvPythonAlt = isWin
        ? path.join(backendDir, 'venv', 'Scripts', 'python3.exe')
        : path.join(backendDir, 'venv', 'bin', 'python');
      if (fs.existsSync(venvPythonAlt)) {
        backendExecutable = venvPythonAlt;
      } else {
        // Fallback to system python
        log('No venv found, falling back to system python');
        backendExecutable = isWin ? 'python' : 'python3';
      }
    }
    backendArgs = ['main.py'];
  }

  log(`Starting backend: ${backendExecutable} ${backendArgs.join(' ')}`);
  log(`Backend dir: ${cwd}`);

  try {
    backendProcess = spawn(backendExecutable, backendArgs, {
      cwd: cwd,
      detached: false,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' }
    });

    log(`Backend spawned with PID: ${backendProcess.pid}`);

    backendProcess.stdout.on('data', (data) => {
      log(`[backend] ${data.toString().trim()}`);
    });

    backendProcess.stderr.on('data', (data) => {
      log(`[backend-err] ${data.toString().trim()}`);
    });

    backendProcess.on('error', (err) => {
      log(`Failed to start backend: ${err.message}`);
    });

    backendProcess.on('exit', (code, signal) => {
      log(`Backend exited (code=${code}, signal=${signal})`);
      backendProcess = null;
    });
  } catch (err) {
    log(`startBackend exception: ${err.message}\n${err.stack}`);
  }
}

function stopBackend() {
  if (backendProcess) {
    log('Stopping backend...');
    if (isWin) {
      // On Windows, SIGTERM is not reliable — use taskkill to kill process tree
      try { execSync(`taskkill /F /T /PID ${backendProcess.pid}`); } catch (e) {}
      backendProcess = null;
    } else {
      backendProcess.kill('SIGTERM');
      setTimeout(() => {
        if (backendProcess) {
          backendProcess.kill('SIGKILL');
          backendProcess = null;
        }
      }, 3000);
    }
  }
  // Also kill anything lingering on port 8000
  killExistingBackend();
}

// Wait for backend health endpoint
function waitForBackend(retries = 30) {
  return new Promise((resolve) => {
    let attempts = 0;
    const check = () => {
      attempts++;
      const req = http.get('http://127.0.0.1:8000/health', (res) => {
        if (res.statusCode === 200) {
          resolve(true);
        } else if (attempts < retries) {
          setTimeout(check, 500);
        } else {
          resolve(false);
        }
      });
      req.on('error', () => {
        if (attempts < retries) {
          setTimeout(check, 500);
        } else {
          resolve(false);
        }
      });
      req.setTimeout(2000, () => {
        req.destroy();
        if (attempts < retries) {
          setTimeout(check, 500);
        } else {
          resolve(false);
        }
      });
    };
    check();
  });
}

// Create main window
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: false, // Allow cross-origin requests to GoPro camera (10.5.5.9) for HLS streaming
      preload: path.join(__dirname, 'preload.js')
    },
    title: 'GoPro Control Center'
  });

  const startUrl = process.env.ELECTRON_START_URL || `file://${path.join(__dirname, '../build/index.html')}`;
  mainWindow.loadURL(startUrl);

  if (process.env.ELECTRON_START_URL) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// App ready
app.whenReady().then(async () => {
  log('=== App starting ===');
  log(`isPackaged: ${app.isPackaged}`);
  log(`platform: ${process.platform}`);
  log(`execPath: ${process.execPath}`);
  log(`__dirname: ${__dirname}`);

  await startBackend();

  const ready = await waitForBackend(30);
  if (ready) {
    log('Backend is ready');
  } else {
    log('Backend did not respond in time, opening app anyway');
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Quit when all windows are closed
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    stopBackend();
    app.quit();
  }
});

// Before quit, clean up backend
app.on('before-quit', () => {
  stopBackend();
});

// IPC handlers
ipcMain.handle('get-app-version', () => {
  return app.getVersion();
});
