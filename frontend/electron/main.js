const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

let mainWindow;
let backendProcess;

// Check if backend is already running
function checkBackendRunning() {
  return new Promise((resolve) => {
    const options = {
      hostname: '127.0.0.1',
      port: 8000,
      path: '/health',
      method: 'GET',
      timeout: 1000
    };

    const req = http.request(options, (res) => {
      resolve(res.statusCode === 200);
    });

    req.on('error', () => {
      resolve(false);
    });

    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });

    req.end();
  });
}

// Start FastAPI backend
async function startBackend() {
  // Check if already running
  const isRunning = await checkBackendRunning();
  if (isRunning) {
    console.log('Backend is already running on port 8000, skipping startup');
    return;
  }

  console.log('Starting backend...');

  // Check for bundled backend (production) first
  const fs = require('fs');
  let backendExecutable;
  let backendArgs = [];
  let cwd;

  // In production, backend is in Resources folder
  // In development, backend is in ../../backend
  const isDev = process.env.ELECTRON_START_URL !== undefined;

  let bundledBackendMac, bundledBackendWin;

  if (isDev) {
    // Development mode - backend is in project directory
    const backendPath = path.join(__dirname, '../../backend');
    bundledBackendMac = path.join(backendPath, 'dist/gopro-backend/gopro-backend');
    bundledBackendWin = path.join(backendPath, 'dist/gopro-backend/gopro-backend.exe');
  } else {
    // Production mode - backend is in app Resources folder
    // process.resourcesPath is the Resources folder in the .app bundle
    const resourcesPath = process.resourcesPath;
    bundledBackendMac = path.join(resourcesPath, 'backend/dist/gopro-backend/gopro-backend');
    bundledBackendWin = path.join(resourcesPath, 'backend/dist/gopro-backend/gopro-backend.exe');
  }

  if (fs.existsSync(bundledBackendMac)) {
    // Production mode - use bundled backend (macOS)
    console.log('Using bundled backend (macOS)');
    backendExecutable = bundledBackendMac;
    cwd = path.dirname(bundledBackendMac);
  } else if (fs.existsSync(bundledBackendWin)) {
    // Production mode - use bundled backend (Windows)
    console.log('Using bundled backend (Windows)');
    backendExecutable = bundledBackendWin;
    cwd = path.dirname(bundledBackendWin);
  } else {
    // Development mode - use virtual environment Python
    console.log('Using development mode (Python venv)');
    const backendPath = path.join(__dirname, '../../backend');
    if (process.platform === 'win32') {
      backendExecutable = path.join(backendPath, 'venv', 'Scripts', 'python.exe');
    } else {
      backendExecutable = path.join(backendPath, 'venv', 'bin', 'python3');
    }
    backendArgs = ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'];
    cwd = backendPath;
  }

  console.log(`Backend executable: ${backendExecutable}`);
  console.log(`Backend working directory: ${cwd}`);
  console.log(`Backend exists: ${fs.existsSync(backendExecutable)}`);

  try {
    backendProcess = spawn(backendExecutable, backendArgs, {
      cwd: cwd,
      detached: false,
      stdio: ['ignore', 'pipe', 'pipe']
    });

    console.log(`Backend process spawned with PID: ${backendProcess.pid}`);

    backendProcess.stdout.on('data', (data) => {
      console.log(`Backend: ${data}`);
    });

    backendProcess.stderr.on('data', (data) => {
      console.error(`Backend Error: ${data}`);
    });

    backendProcess.on('close', (code) => {
      console.log(`Backend process exited with code ${code}`);
    });

    backendProcess.on('error', (err) => {
      console.error(`Failed to start backend process: ${err.message}`);
      console.error(`Error code: ${err.code}`);
    });
  } catch (err) {
    console.error(`Exception while starting backend: ${err}`);
  }
}

// Create main window
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    },
    title: 'GoPro Control Center'
  });

  // Load React app
  const startUrl = process.env.ELECTRON_START_URL || `file://${path.join(__dirname, '../build/index.html')}`;
  mainWindow.loadURL(startUrl);

  // Open DevTools in development
  if (process.env.ELECTRON_START_URL) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// App ready
app.whenReady().then(async () => {
  // Start backend (or detect if already running)
  await startBackend();

  // Wait a bit for backend to be ready, then create window
  setTimeout(() => {
    createWindow();
  }, 2000);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Quit when all windows are closed
app.on('window-all-closed', () => {
  // Kill backend process only if we started it
  if (backendProcess) {
    console.log('Stopping backend process...');
    backendProcess.kill();
  }

  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// Before quit, clean up backend
app.on('before-quit', () => {
  // Only kill if we started it
  if (backendProcess) {
    console.log('Cleaning up backend process...');
    backendProcess.kill();
  }
});

// IPC handlers (if needed for future features)
ipcMain.handle('get-app-version', () => {
  return app.getVersion();
});
