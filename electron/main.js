const { app, BrowserWindow, dialog, ipcMain, Menu } = require('electron');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
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
let isShuttingDown = false;

// Log buffer: array of {ts, level, text}
const logBuffer = [];

// ---------- 日志落盘（排查定时同步/LLM 失败必需）----------
// 之前的日志只存在内存 logBuffer 里，程序一关就没了，事后无法复盘失败原因。
// 现在同时追加写到 <userData>/logs/backend-YYYY-MM-DD.log，保留 LOG_KEEP_DAYS 天。
const LOG_KEEP_DAYS = 14;
const LOG_FILE_RE = /^backend-\d{4}-\d{2}-\d{2}\.log$/;
let logStream = null;
let logStreamDate = null;

function getLogDir() {
  return path.join(app.getPath('userData'), 'logs');
}

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function cleanupOldLogs(dir) {
  try {
    const cutoff = Date.now() - LOG_KEEP_DAYS * 24 * 3600 * 1000;
    for (const f of fs.readdirSync(dir)) {
      if (!LOG_FILE_RE.test(f)) continue;
      const fp = path.join(dir, f);
      try {
        if (fs.statSync(fp).mtimeMs < cutoff) fs.unlinkSync(fp);
      } catch (e) { /* 单个文件删除失败忽略 */ }
    }
  } catch (e) { /* 目录不存在等忽略 */ }
}

function getLogStream() {
  const day = todayStr();
  if (logStream && logStreamDate === day) return logStream;
  try {
    if (logStream) { try { logStream.end(); } catch (e) {} logStream = null; }
    const dir = getLogDir();
    fs.mkdirSync(dir, { recursive: true });
    logStream = fs.createWriteStream(path.join(dir, `backend-${day}.log`), { flags: 'a' });
    logStreamDate = day;
    cleanupOldLogs(dir);
  } catch (e) {
    logStream = null; // 落盘失败不影响主流程
  }
  return logStream;
}

function writeLogFile(level, text) {
  const s = getLogStream();
  if (!s) return;
  try {
    const ts = new Date().toISOString();
    const body = String(text).split('\n').map((l) => `[${ts}] [${level}] ${l}`).join('\n');
    s.write(body + '\n');
  } catch (e) { /* 忽略写失败 */ }
}

function closeLogFile() {
  if (logStream) {
    try { logStream.end(); } catch (e) {}
    logStream = null;
    logStreamDate = null;
  }
}

function addLog(level, text) {
  const line = { ts: new Date().toISOString(), level, text };
  logBuffer.push(line);
  if (logBuffer.length > MAX_LOG_LINES) logBuffer.shift();
  writeLogFile(level, text);
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

// 启动前检查：是否已有本程序实例在运行（占用 8765 端口）。
// 设计原则（2026-08-29 简化，采纳用户意见）：只「检测 + 提示」，绝不替使用者杀进程。
// 之前按镜像名 taskkill 清理旧实例反复翻车 —— 便携版下会误杀自己的父进程（启动器），
// 连同自身一起带走，导致「双击便携包毫无反应」。杀进程逻辑全部移除，交给使用者处理。
const APP_MAIN_IMAGE = 'Personal AI Dev Center.exe';

function isAnotherInstanceRunning() {
  if (process.platform !== 'win32') return false;
  try {
    const out = execSync('netstat -ano -p TCP | findstr ":8765"', {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).toString();
    const pids = new Set();
    out.split(/\r?\n/).forEach((line) => {
      const m = line.trim().match(/:8765\b.*?LISTENING\s+(\d+)/i);
      if (m) pids.add(parseInt(m[1], 10));
    });
    if (pids.size === 0) return false;
    for (const pid of pids) {
      if (!pid || pid === process.pid) continue;
      // 端口被「本程序主进程」占用 -> 确认已有实例在跑
      try {
        const img = execSync(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`, {
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'ignore'],
        }).toString();
        if (img.includes(APP_MAIN_IMAGE)) return true;
      } catch (e) {
        // 进程已退出，忽略
      }
    }
    // 端口被其它进程占用（极少见），也视为冲突，提示使用者排查
    return true;
  } catch (e) {
    return false; // netstat 无结果 -> 端口空闲
  }
}

function startBackend() {
  const resourceDir = getResourceDir();
  // R5: 打包后优先用内置 exe（自包含 Python，目标机无需安装 Python）
  const bundledExe = path.join(resourceDir, 'backend', 'devcenter-backend.exe');
  const useBundled = fs.existsSync(bundledExe);

  let command, args, env;
  if (useBundled) {
    console.log(`[Electron] Starting bundled backend: ${bundledExe}`);
    addLog('info', `[Electron] Starting bundled backend: ${bundledExe}`);
    command = bundledExe;
    args = [];
    const dataDir = path.join(
      process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'),
      'Personal AI Dev Center', 'data'
    );
    env = {
      ...process.env,
      DEV_CENTER_PACKAGED: '1',
      DEV_CENTER_RESOURCE_DIR: resourceDir,
      DEV_CENTER_DATA_DIR: dataDir,
      DEV_CENTER_APP_VERSION: APP_VERSION,
      PYTHONIOENCODING: 'utf-8',
    };
  } else {
    // dev / 回退：用系统 python 运行 backend/app.py
    const backendScript = path.join(resourceDir, 'backend', 'app.py');
    console.log(`[Electron] Starting backend: python ${backendScript}`);
    console.log(`[Electron] Working directory: ${resourceDir}`);
    addLog('info', `[Electron] Starting backend: python ${backendScript}`);
    command = 'python';
    args = [backendScript];
    env = { ...process.env, DEV_CENTER_APP_VERSION: APP_VERSION, PYTHONIOENCODING: 'utf-8' };
  }

  backendProcess = spawn(command, args, {
    cwd: resourceDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env,
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
      `无法启动后端服务。\n（打包版内置运行环境；若用源码运行请确认已安装 Python 并加入 PATH。）\n\n错误: ${err.message}`
    );
    app.quit();
  });

  backendProcess.on('exit', (code) => {
    console.log(`[Electron] Backend exited with code: ${code}`);
    addLog('info', `[Electron] Backend exited with code: ${code}`);
    if (isShuttingDown) return; // 主动关闭，不弹窗
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

// 退出时同步强杀后端进程树，确保端口 8765 在程序关闭前释放（避免残留孤儿后端占用端口）
function killBackend() {
  if (!backendProcess) return;
  const pid = backendProcess.pid;
  const alreadyKilled = backendProcess.killed;
  backendProcess = null;
  if (alreadyKilled) return;
  isShuttingDown = true;
  console.log(`[Electron] Killing backend process (pid ${pid})...`);
  addLog('info', `[Electron] Killing backend process (pid ${pid})...`);
  try {
    if (process.platform === 'win32') {
      // 同步执行：必须等后端真正退出再继续，否则 electron 先走完退出流程会留下孤儿
      execSync(`taskkill /PID ${pid} /T /F`, {
        windowsHide: true,
        stdio: ['ignore', 'ignore', 'ignore'],
      });
    } else {
      process.kill(pid, 'SIGTERM');
    }
  } catch (e) {
    // 多数情况是进程已退出，忽略
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

// 启动前检查：已有实例在跑则提示使用者关闭、自己退出（绝不替他杀进程）
if (isAnotherInstanceRunning()) {
  dialog.showErrorBox(
    '已在运行 / Already Running',
    'Personal AI Dev Center 已经在运行中（端口 8765 被占用）。\n\n' +
    '请先关闭已运行的实例，再重新打开本程序。\n\n' +
    '若端口被上一次残留的后端进程占用（无界面窗口），可在 PowerShell 执行：\n' +
    '    Stop-Process -Name "devcenter-backend" -Force\n' +
    '释放端口后再双击本程序即可。'
  );
  app.quit();
} else {
  // 单实例锁：再次兜底，防止极快连点产生多个窗口
  if (!app.requestSingleInstanceLock()) {
    app.quit();
  }
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

app.on('ready', async () => {
  Menu.setApplicationMenu(Menu.buildFromTemplate(menuTemplate));
  addLog('info', `[Electron] === 会话开始 (v${APP_VERSION}) ===`);
  try {
    addLog('info', `[Electron] 日志文件: ${path.join(getLogDir(), `backend-${todayStr()}.log`)}`);
  } catch (e) { /* 忽略 */ }
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
      `后端服务未能在 ${MAX_WAIT_MS / 1000} 秒内启动。\n\n可能原因:\n1. 端口 ${PORT} 被占用\n2. 后端未正确启动（内置运行环境损坏）\n3. 配置错误\n\n请检查后重试。`
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

// 兜底：任何退出路径都再确保一次后端已杀（含 window-all-closed 之外的异常退出）
app.on('quit', () => {
  killBackend();
  addLog('info', `[Electron] === 会话结束 ===`);
  closeLogFile();
});

// 终极兜底：主进程被强杀时也尽量同步终结后端，释放 8765
process.on('exit', () => {
  if (backendProcess && !backendProcess.killed) {
    try { backendProcess.kill('SIGKILL'); } catch (e) {}
  }
  closeLogFile();
});
