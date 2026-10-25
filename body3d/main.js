// 大肥鱼的 3D 身体（Electron 主进程）
// 大脑仍是 pet/ 里的 Python：本进程每 200ms 去 127.0.0.1:8765/v1/pet/state 拿「此刻的她」，
// 由主进程去取（不受网页跨域限制），所以 Python 那边不需要开放跨域，网页读不到她的状态。
const { app, BrowserWindow, screen, ipcMain, shell, Menu } = require('electron');
const path = require('path');

const SMOKE = process.argv.includes('--smoke');
// 用户数据（缓存、GPU 缓存等）放在身体目录里，不写 %APPDATA%（受限环境里那里可能没有写权限）
app.setPath('userData', path.join(__dirname, '.userdata'));
const BRAIN = process.env.DFY_BRAIN || 'http://127.0.0.1:8765';
const SIZE = { width: 420, height: 560 };

let win = null;
let drag = null; // { cursor0, win0 }

function createWindow() {
  const area = screen.getPrimaryDisplay().workArea;
  win = new BrowserWindow({
    ...SIZE,
    // DFY_BODY_POS="x,y" 可指定初始位置（测试截图用）；默认右下角
    x: process.env.DFY_BODY_POS ? Number(process.env.DFY_BODY_POS.split(',')[0]) : area.x + area.width - SIZE.width - 40,
    y: process.env.DFY_BODY_POS ? Number(process.env.DFY_BODY_POS.split(',')[1]) : area.y + area.height - SIZE.height,
    transparent: true,
    frame: false,
    resizable: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: !SMOKE,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      additionalArguments: [...(SMOKE ? ['--dfy-smoke'] : []), ...(process.argv.some((a) => a.startsWith('--shot=')) ? ['--dfy-shot'] : [])],
    },
  });
  win.setIgnoreMouseEvents(true, { forward: true }); // 默认点穿，鼠标在身体上时渲染端会关掉
  win.loadFile(path.join(__dirname, 'index.html'));
  if (SMOKE) {
    setTimeout(() => { console.log('BODY3D SMOKE FAIL: 60 秒内没完成'); app.exit(1); }, 60000);
  }
  // --shot=路径：可见运行，约 6 秒后渲染端把画布内容发回来存成 PNG 再退出（检查 3D 效果用）
  const shotArg = process.argv.find((a) => a.startsWith('--shot='));
  if (shotArg) {
    ipcMain.once('shot-data', (_e, dataUrl) => {
      require('fs').writeFileSync(shotArg.slice(7), Buffer.from(String(dataUrl).split(',')[1] || '', 'base64'));
      console.log('BODY3D SHOT SAVED');
      app.exit(0);
    });
    setTimeout(() => { console.log('BODY3D SHOT FAIL'); app.exit(1); }, 30000);
  }
}

// ---------- 鼠标：30Hz 把全局光标和窗口位置发给渲染端（她的眼睛和头会跟着看）----------
function tickCursor() {
  if (!win || win.isDestroyed()) return;
  const p = screen.getCursorScreenPoint();
  const b = win.getBounds();
  if (drag) {
    win.setPosition(Math.round(drag.win0.x + p.x - drag.cursor0.x), Math.round(drag.win0.y + p.y - drag.cursor0.y));
  }
  win.webContents.send('cursor', { x: p.x - b.x, y: p.y - b.y, w: b.width, h: b.height });
}

// ---------- 大脑：状态与事件 ----------
async function pollState() {
  if (!win || win.isDestroyed()) return;
  try {
    const res = await fetch(`${BRAIN}/v1/pet/state`, { signal: AbortSignal.timeout(800) });
    const data = await res.json();
    win.webContents.send('pet-state', data);
  } catch {
    win.webContents.send('pet-state', { ok: false });
  }
}

async function sendEvent(kind) {
  try {
    await fetch(`${BRAIN}/v1/pet/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind }),
      signal: AbortSignal.timeout(1500),
    });
  } catch {
    /* 大脑没开：点了也只是本地动一下 */
  }
}

ipcMain.on('set-ignore', (_e, ignore) => {
  if (win && !win.isDestroyed()) win.setIgnoreMouseEvents(Boolean(ignore), { forward: true });
});
ipcMain.on('drag-start', () => {
  if (!win) return;
  const b = win.getBounds();
  drag = { cursor0: screen.getCursorScreenPoint(), win0: { x: b.x, y: b.y } };
});
ipcMain.on('drag-end', () => { drag = null; });
ipcMain.on('pet-event', (_e, kind) => sendEvent(String(kind || '')));
ipcMain.on('open-chat', () => shell.openExternal(`${BRAIN}/chat`));
ipcMain.on('context-menu', () => {
  const feed = ['rice:🍚 白饭', 'fish:🐟 小鱼干', 'cake:🍰 蛋糕', 'tangyuan:🍡 团子', 'diamond:💎 钻石'].map((item) => {
    const [food, label] = item.split(':');
    return { label, click: () => sendEvent(`feed:${food}`) };
  });
  Menu.buildFromTemplate([
    { label: '🤚 摸摸头', click: () => sendEvent('pat') },
    { label: '🍽 投喂', submenu: feed },
    { label: '🗨 和她说话（网页）', click: () => shell.openExternal(`${BRAIN}/chat`) },
    { type: 'separator' },
    { label: '🖼 回到 2D', click: () => { sendEvent('back_2d'); setTimeout(() => app.quit(), 300); } },
    { label: '✖ 关掉 3D 身体', click: () => app.quit() },
  ]).popup({ window: win });
});
ipcMain.on('smoke-result', (_e, result) => {
  if (!SMOKE) return;
  console.log(result && result.ok ? `BODY3D SMOKE OK ${result.msg || ''} bounds=${JSON.stringify(win.getBounds())} content=${JSON.stringify(win.getContentBounds())}` : `BODY3D SMOKE FAIL: ${result && result.msg}`);
  app.exit(result && result.ok ? 0 : 1);
});

app.whenReady().then(() => {
  createWindow();
  setInterval(tickCursor, 33);
  setInterval(pollState, 200);
});
app.on('window-all-closed', () => app.quit());
