// 用本机 Electron 渲染 index.html，给「首页同步状态条」的各种形态截图。
// 目的：验证视觉改动（原来一整块 -> 现在一行），并留下可对比的证据。
//
// 用法: node_modules/electron/dist/electron.exe tools/screenshot_syncbar.js
// 输出: %TEMP%/padc_shots/*.png  (可用 SHOT_DIR 覆盖)

const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const OUT = process.env.SHOT_DIR || path.join(process.env.TEMP || '.', 'padc_shots');
fs.mkdirSync(OUT, { recursive: true });

// 沙箱/无头环境必备：GUI 主进程容易被限制（GPU 进程崩溃 + file:// 被拒）
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-gpu-compositing');
app.commandLine.appendSwitch('disable-software-rasterizer');
app.commandLine.appendSwitch('allow-file-access-from-files');
app.commandLine.appendSwitch('in-process-gpu');

// 看门狗：无论如何 60s 内必须退出，避免挂死
const watchdog = setTimeout(() => { console.error('WATCHDOG: force exit'); process.exit(3); }, 60000);

const now = Date.now();
const DEMO_OK = {
  finished_at: new Date(now - 2 * 3600e3).toISOString(),
  started_at: new Date(now - 2 * 3600e3 - 41000).toISOString(),
  source: 'auto', success: 12, failed: 0, total: 12,
  llm_analyzed: 3, duration_sec: 41, errors: [],
};
const DEMO_BAD = {
  ...DEMO_OK, success: 11, failed: 1,
  errors: [{ name: 'demo-project', error: 'connect timeout after 3 retries' }],
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let win;

async function evalInPage(js) {
  return win.webContents.executeJavaScript(js);
}

async function shot(name, { clip = true, view = null, mode = null, data = null } = {}) {
  const js = [];
  if (mode) js.push(`localStorage.setItem('dc-syncbar-mode', ${JSON.stringify(mode)});`);
  if (data) js.push(`state.lastSync = ${JSON.stringify(data)};`);
  if (view) js.push(`state.view = ${JSON.stringify(view)};`);
  if (js.length) js.push('render();');
  if (js.length) await evalInPage(js.join('\n'));
  await evalInPage('if(window.lucide)lucide.createIcons();');
  await sleep(400);

  if (clip) {
    const rect = await evalInPage(`(() => {
      const el = document.querySelector('.last-sync-bar') || document.querySelector('.lsb-mini');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.max(0, Math.floor(r.x - 10)), y: Math.max(0, Math.floor(r.y - 8)),
               width: Math.ceil(r.width + 20), height: Math.ceil(r.height + 16) };
    })()`);
    if (rect && rect.width > 0 && rect.height > 0) {
      const img = await win.webContents.capturePage(rect);
      fs.writeFileSync(path.join(OUT, name + '.png'), img.toPNG());
      console.log('saved', name + '.png', `${rect.width}x${rect.height}`);
      return;
    }
    console.log('warn: no bar element for', name, '- falling back to full page');
  }
  const img = await win.webContents.capturePage();
  fs.writeFileSync(path.join(OUT, name + '.png'), img.toPNG());
  console.log('saved', name + '.png (full page)');
}

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  win = new BrowserWindow({
    width: 1280, height: 900, show: false,
    webPreferences: { contextIsolation: false, nodeIntegration: false, backgroundThrottling: false },
  });
  const { pathToFileURL } = require('url');
  const target = process.env.SHOT_URL || pathToFileURL(path.join(ROOT, 'index.html')).href;
  console.log('loading', target);
  try {
    await win.loadURL(target);
  } catch (e) {
    console.error('LOAD FAILED:', e && e.message);
    clearTimeout(watchdog);
    app.quit();
    setTimeout(() => process.exit(1), 400);
    return;
  }
  await sleep(1500);

  // 关掉可能弹出来的 toast / modal，保证截图干净
  await evalInPage(`
    const t = document.getElementById('toast-el'); if (t) t.remove();
    state.modal = null; state.view = 'dashboard'; render();
  `);
  await sleep(300);

  try {
    // ① 首页默认态（新增后的样子）：全部成功 -> 压成一行
    await shot('01-home-collapsed-ok', { data: DEMO_OK, mode: 'collapsed', view: 'dashboard' });
    // ② 首页默认态：有失败 -> 一行里直接点出失败数
    await shot('02-home-collapsed-bad', { data: DEMO_BAD, mode: 'collapsed', view: 'dashboard' });
    // ③ 首页展开：要看细节时点开
    await shot('03-home-expanded-bad', { data: DEMO_BAD, mode: 'expanded', view: 'dashboard' });
    // ④ 首页隐藏后只剩的小入口
    await shot('04-home-hidden', { data: DEMO_OK, mode: 'hidden', view: 'dashboard' });
    // ⑤ 首页整体（看它在页面里的占比）
    await shot('05-home-overall', { data: DEMO_OK, mode: 'collapsed', view: 'dashboard', clip: false });
    // ⑥ 定时同步页：保持完整形态，不受首页开关影响
    await shot('06-schedules-full', { data: DEMO_OK, mode: 'hidden', view: 'schedules' });
    console.log('DONE out=' + OUT);
  } catch (e) {
    console.error('ERROR', e && e.message);
    process.exitCode = 1;
  } finally {
    clearTimeout(watchdog);
    app.quit();
    setTimeout(() => process.exit(process.exitCode || 0), 600);
  }
});

app.on('window-all-closed', () => app.quit());
