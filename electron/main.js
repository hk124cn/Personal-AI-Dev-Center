const { app, BrowserWindow, dialog, ipcMain, Menu } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

// 从 package.json 读取版本号，保证与发布版本一致
const pkg = require('../package.json');
const APP_VERSION = (pkg && pkg.version) || '1.0.0';

const PORT = 8765;
const BACKEND_URL = `http://localhost:${PORT}`;
const HEALTH_URL = `${BACKEND_URL}/api/health`;
const MAX_WAIT_MS = 20000;
const MAX_LOG_LINES = 5000;

let mainWindow = null;
let backendProcess = null;

// Log buffer: array of {ts, level, text}
const logBuffer = [];

function addLog(level, text) {
  const line = { ts: new Date().toISOString(), level, text };
  logBuffer.push(line);
  if (logBuffer.length > MAX_LOG_LINES) logBuffer.shift();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('log-line', line);
  }
}

// Determine resource directory (packaged vs dev mode)
function getResourceDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath);
  }
  return path.join(__dirname, '..');
}

function startBackend() {
  const resourceDir = getResourceDir();
  const backendScript = path.join(resourceDir, 'backend', 'app.py');

  console.log(`[Electron] Starting backend: python ${backendScript}`);
  console.log(`[Electron] Working directory: ${resourceDir}`);
  addLog('info', `[Electron] Starting backend: python ${backendScript}`);

  backendProcess = spawn('python', [backendScript], {
    cwd: resourceDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });

  backendProcess.stdout.on('data', (data) => {
    const text = data.toString().trim();
    if (text) {
      console.log(`[Backend] ${text}`);
      text.split('\n').forEach(line => addLog('stdout', line));
    }
  });

  backendProcess.stderr.on('data', (data) => {
    const text = data.toString().trim();
    if (text) {
      console.error(`[Backend ERR] ${text}`);
      text.split('\n').forEach(line => addLog('stderr', line));
    }
  });

  backendProcess.on('error', (err) => {
    console.error(`[Electron] Failed to start Python: ${err.message}`);
    addLog('error', `[Electron] Failed to start Python: ${err.message}`);
    dialog.showErrorBox(
      '启动失败 / Startup Failed',
      `无法启动 Python 后端。\n请确认已安装 Python 并加入 PATH。\n\n错误: ${err.message}`
    );
    app.quit();
  });

  backendProcess.on('exit', (code) => {
    console.log(`[Electron] Backend exited with code: ${code}`);
    addLog('info', `[Electron] Backend exited with code: ${code}`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog.showMessageBox(mainWindow, {
        type: 'warning',
        title: '后端已退出 / Backend Exited',
        message: `后端进程意外退出 (code: ${code})。\n程序将自动关闭。`,
      });
      app.quit();
    }
  });
}

function waitForBackend() {
  return new Promise((resolve, reject) => {
    const startTime = Date.now();

    function check() {
      if (Date.now() - startTime > MAX_WAIT_MS) {
        reject(new Error(`Backend not ready within ${MAX_WAIT_MS / 1000}s`));
        return;
      }

      const req = http.get(HEALTH_URL, (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else {
          setTimeout(check, 500);
        }
      });

      req.on('error', () => {
        setTimeout(check, 500);
      });

      req.setTimeout(2000, () => {
        req.destroy();
        setTimeout(check, 500);
      });
    }

    check();
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: 'Personal AI Dev Center',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
    show: false,
  });

  mainWindow.loadURL(BACKEND_URL);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function killBackend() {
  if (backendProcess && !backendProcess.killed) {
    console.log('[Electron] Killing backend process...');
    addLog('info', '[Electron] Killing backend process...');
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(backendProcess.pid), '/T', '/F'], {
        windowsHide: true,
      });
    } else {
      backendProcess.kill('SIGTERM');
    }
    backendProcess = null;
  }
}

// IPC handlers for log viewer
ipcMain.handle('get-logs', () => logBuffer);
ipcMain.handle('clear-logs', () => { logBuffer.length = 0; return true; });

// 原生目录选择对话框（替代后端 tkinter，打包后更可靠、不依赖 Python GUI 库）
ipcMain.handle('dialog:pickDirectory', async () => {
  if (!mainWindow) return '';
  try {
    const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
      title: '选择项目本地目录',
      properties: ['openDirectory', 'createDirectory'],
    });
    if (canceled || !filePaths || filePaths.length === 0) return '';
    return filePaths[0];
  } catch (e) {
    console.error('[Electron] pickDirectory failed:', e);
    return '';
  }
});

// ==================== MENU BAR (Chinese) ====================
const menuTemplate = [
  {
    label: '文件',
    submenu: [
      { label: '刷新', accelerator: 'CmdOrCtrl+R', click: () => { if (mainWindow) mainWindow.webContents.reload(); } },
      { type: 'separator' },
      { label: '退出', accelerator: 'CmdOrCtrl+Q', role: 'quit' },
    ]
  },
  {
    label: '编辑',
    submenu: [
      { label: '撤销', accelerator: 'CmdOrCtrl+Z', role: 'undo' },
      { label: '重做', accelerator: 'CmdOrCtrl+Y', role: 'redo' },
      { type: 'separator' },
      { label: '剪切', accelerator: 'CmdOrCtrl+X', role: 'cut' },
      { label: '复制', accelerator: 'CmdOrCtrl+C', role: 'copy' },
      { label: '粘贴', accelerator: 'CmdOrCtrl+V', role: 'paste' },
      { label: '删除', role: 'delete' },
      { type: 'separator' },
      { label: '全选', accelerator: 'CmdOrCtrl+A', role: 'selectAll' },
    ]
  },
  {
    label: '视图',
    submenu: [
      { label: '开发者工具', accelerator: 'F12', role: 'toggleDevTools' },
      { type: 'separator' },
      { label: '实际大小', accelerator: 'CmdOrCtrl+0', role: 'resetZoom' },
      { label: '放大', accelerator: 'CmdOrCtrl+=', role: 'zoomIn' },
      { label: '缩小', accelerator: 'CmdOrCtrl+-', role: 'zoomOut' },
      { type: 'separator' },
      { label: '全屏', accelerator: 'F11', role: 'togglefullscreen' },
    ]
  },
  {
    label: '窗口',
    submenu: [
      { label: '最小化', accelerator: 'CmdOrCtrl+M', role: 'minimize' },
      { label: '关闭', accelerator: 'CmdOrCtrl+W', role: 'close' },
    ]
  },
  {
    label: '帮助',
    submenu: [
      {
        label: '关于 Dev Center',
        click: () => {
          dialog.showMessageBox(mainWindow, {
            type: 'info',
            title: '关于',
            message: 'Personal AI Dev Center',
            detail: `个人 AI 开发中心\n多服务器管理与项目开发面板\n\n版本 ${APP_VERSION}`,
          });
        }
      },
    ]
  },
];

// --- App lifecycle ---

app.on('ready', async () => {
  Menu.setApplicationMenu(Menu.buildFromTemplate(menuTemplate));
  startBackend();

  try {
    console.log('[Electron] Waiting for backend to be ready...');
    addLog('info', '[Electron] Waiting for backend to be ready...');
    await waitForBackend();
    console.log('[Electron] Backend is ready, creating window...');
    addLog('info', '[Electron] Backend is ready, creating window...');
    createWindow();
  } catch (err) {
    console.error(`[Electron] ${err.message}`);
    addLog('error', `[Electron] ${err.message}`);
    dialog.showErrorBox(
      '启动超时 / Startup Timeout',
      `后端服务未能在 ${MAX_WAIT_MS / 1000} 秒内启动。\n\n可能原因:\n1. 端口 ${PORT} 被占用\n2. Python 依赖未安装\n3. 配置错误\n\n请检查后重试。`
    );
    killBackend();
    app.quit();
  }
});

app.on('window-all-closed', () => {
  killBackend();
  app.quit();
});

app.on('before-quit', () => {
  killBackend();
});
