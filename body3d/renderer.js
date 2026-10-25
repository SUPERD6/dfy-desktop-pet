// 大肥鱼的 3D 身体（渲染端）：加载 VRM，呼吸、眨眼、看鼠标、跟着大脑的情绪做表情、说话时动嘴。
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const dfy = window.dfy;
const bubbleEl = document.getElementById('bubble');
const W = window.innerWidth;
const H = window.innerHeight;

// ---------- 场景 ----------
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(W, H);
renderer.setClearColor(0x000000, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(24, W / H, 0.1, 20);
const ambient = new THREE.AmbientLight(0xffffff, 1.6);
scene.add(ambient);
const sun = new THREE.DirectionalLight(0xffffff, 2.0);
sun.position.set(1, 2, 3);
scene.add(sun);

// 情绪（大脑那边的桌宠状态）→ VRM 表情和强度
const EXPRESSIONS = {
  happy: { happy: 0.9 }, proud: { happy: 0.6 }, shy: { happy: 0.45, relaxed: 0.3 },
  angry: { angry: 0.8 }, sad: { sad: 0.8 }, hungry: { sad: 0.35 },
  surprised: { surprised: 0.85 }, confused: { surprised: 0.3, sad: 0.15 },
  sleepy: { relaxed: 0.7 }, talking: {}, idle: {}, thinking: { relaxed: 0.2 },
};
const EXPRESSION_NAMES = ['happy', 'angry', 'sad', 'relaxed', 'surprised'];

let vrm = null;
let lookTarget = new THREE.Object3D();
scene.add(lookTarget);
const state = { emotion: 'idle', speaking: false, bubble: '', sleepy: false };
const current = Object.fromEntries(EXPRESSION_NAMES.map((n) => [n, 0]));
let cursor = { x: W / 2, y: H / 3, inside: false };
let blinkTimer = 2 + Math.random() * 3;
let blinkPhase = 0;
let pokeUntil = 0;

// ---------- 加载模型 ----------
// 顺序：models/model.vrm（带骨骼表情的正式形象）> model.glb / mingyue_q.glb（图生 3D 的整体模型）> sample.vrm（示例）
let simple = null; // 没有骨骼的整体模型：{ root, pivot, baseScale }
const MODEL_ORDER = ['./models/model.vrm', './models/model.glb', './models/mingyue_q.glb', './models/sample.vrm'];

async function loadVRM() {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  let gltf = null;
  let url = '';
  for (url of MODEL_ORDER) {
    try { gltf = await loader.loadAsync(url); break; } catch { gltf = null; }
  }
  if (!gltf) throw new Error('models/ 里没有能用的 .vrm 或 .glb 模型');
  if (!gltf.userData.vrm) { simple = await setupSimple(gltf.scene, url); return null; }
  const model = gltf.userData.vrm;
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  if (VRMUtils.combineSkeletons) VRMUtils.combineSkeletons(gltf.scene);
  if (model.meta && model.meta.metaVersion === '0') VRMUtils.rotateVRM0(model); // 0.x 模型朝向不同
  model.scene.traverse((o) => { o.frustumCulled = false; });
  scene.add(model.scene);
  if (model.lookAt) model.lookAt.target = lookTarget;
  relaxArms(model);
  frameCamera(model);
  return model;
}

// ---------- 整体模型（图生 3D，没有骨骼）：摆正、脚踩在原点、统一身高，动作全靠整体变换 ----------
async function setupSimple(root, url) {
  let opts = {};
  try { opts = await (await fetch(url.replace(/\.glb$/, '.json'))).json(); } catch { /* 没有就用默认 */ }
  const rot = (opts.rotation || [0, 0, 0]).map((d) => THREE.MathUtils.degToRad(d));
  root.rotation.set(rot[0], rot[1], rot[2]);
  root.traverse((o) => {
    if (o.isMesh) {
      o.frustumCulled = false;
      const color = o.geometry.attributes.color;
      if (color && !o.geometry.userData.linearized) {
        // 图生 3D 导出的顶点色是 sRGB，渲染器按线性色算 → 发灰发白；先换算成线性
        const c = new THREE.Color();
        for (let i = 0; i < color.count; i += 1) {
          c.setRGB(color.getX(i), color.getY(i), color.getZ(i), THREE.SRGBColorSpace);
          color.setXYZ(i, c.r, c.g, c.b);
        }
        color.needsUpdate = true;
        o.geometry.userData.linearized = true;
      }
      // 二次元配色：不要太多高光，用 Lambert + 顶点颜色
      o.material = new THREE.MeshLambertMaterial({ vertexColors: Boolean(color), color: color ? 0xffffff : 0x9fb8e8 });
    }
  });
  const holder = new THREE.Group();
  holder.add(root);
  holder.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(holder);
  const size = box.getSize(new THREE.Vector3());
  const scale = 1.0 / size.y; // 统一成 1 个单位高
  root.position.set(-(box.min.x + size.x / 2), -box.min.y, -(box.min.z + size.z / 2));
  ambient.intensity = 1.25; // 整体模型只有顶点色，灯太亮会把配色冲淡
  sun.intensity = 1.1;
  const pivot = new THREE.Group(); // 脚底为原点，转身、压扁都以脚为支点
  pivot.scale.setScalar(scale);
  pivot.add(holder);
  scene.add(pivot);
  frameCamera.center = new THREE.Vector3(0, 0.5, 0);
  frameCamera.height = 1.0;
  const fov = THREE.MathUtils.degToRad(camera.fov);
  // 高度和宽度都要装下（Q 版头发、尾巴往两边张，宽度常和身高差不多，窗口却是竖的）
  const width = (Math.max(size.x, size.z) * scale) / camera.aspect;
  const span = Math.max(1.0 / 0.78, width / 0.9);
  const dist = span / (2 * Math.tan(fov / 2));
  const lift = span * 0.09; // 镜头略抬，头顶留出气泡位置，脚不贴底
  camera.position.set(0, 0.5 + lift, dist);
  camera.lookAt(0, 0.5 + lift, 0);
  return { root, pivot, baseScale: scale };
}

function animateSimple(dt, t) {
  const p = simple.pivot;
  const k = simple.baseScale;
  const nx = THREE.MathUtils.clamp((cursor.x / W) * 2 - 1, -1.5, 1.5);
  const ny = THREE.MathUtils.clamp(1 - (cursor.y / H) * 2, -1.2, 1.2);
  let yaw = THREE.MathUtils.clamp(nx * 0.45, -0.45, 0.45); // 整个人转向鼠标
  let pitch = THREE.MathUtils.clamp(-ny * 0.06, -0.08, 0.08);
  let roll = 0;
  let hop = 0;
  let sx = 1; let sy = 1 + Math.sin(t * 1.6) * 0.012; // 呼吸
  switch (state.emotion) {
    case 'happy': case 'proud': hop = Math.max(0, Math.sin(t * 6)) * 0.035; pitch -= state.emotion === 'proud' ? 0.06 : 0; break;
    case 'sad': case 'hungry': pitch += 0.1; sy *= 0.97; break;
    case 'angry': roll = Math.sin(t * 32) * 0.025; break;
    case 'confused': roll = 0.14; break;
    case 'surprised': sy *= 1.04; hop = 0.015; break;
    case 'sleepy': roll = Math.sin(t * 0.8) * 0.06; pitch += 0.07; break;
    case 'shy': yaw *= 0.3; roll = -0.06; pitch += 0.05; break;
    default: break;
  }
  if (state.speaking) hop += Math.abs(Math.sin(t * 10)) * 0.012;
  const poke = Math.max(0, pokeUntil - t) / 0.8; // 被戳：先压扁再弹回
  if (poke > 0) { const e = Math.sin(poke * Math.PI); sy *= 1 - 0.14 * e; sx *= 1 + 0.08 * e; }
  const f = Math.min(1, dt * 5);
  p.rotation.y += (yaw - p.rotation.y) * f;
  p.rotation.x += (pitch - p.rotation.x) * f;
  p.rotation.z += (roll - p.rotation.z) * f;
  p.position.y = hop;
  p.scale.set(k * sx, k * sy, k * sx);
}

// T 字姿势的手臂放下来，自然一点
function relaxArms(model) {
  const h = model.humanoid;
  const set = (name, z, x = 0) => { const b = h.getNormalizedBoneNode(name); if (b) { b.rotation.z = z; b.rotation.x = x; } };
  set('leftUpperArm', -1.15, 0.05);
  set('rightUpperArm', 1.15, 0.05);
  set('leftLowerArm', -0.2);
  set('rightLowerArm', 0.2);
}

// 镜头对准全身：按骨骼（头到脚）取景。包围盒不可靠——有的模型带额外节点，盒子比人高很多，
// 实测示例模型就因此显得又小又低、脚被任务栏挡住
function frameCamera(model) {
  model.scene.updateMatrixWorld(true);
  const h = model.humanoid;
  const worldY = (name) => {
    const node = h.getNormalizedBoneNode(name) || h.getRawBoneNode(name);
    return node ? node.getWorldPosition(new THREE.Vector3()) : null;
  };
  const head = worldY('head');
  const foot = worldY('leftFoot') || worldY('rightFoot');
  let top; let bottom; let cx = 0; let cz = 0;
  if (head && foot) {
    const headToFoot = head.y - foot.y;
    top = head.y + headToFoot * 0.16;      // 头骨在脖子上方，头顶再往上一截（含头发）
    bottom = foot.y - headToFoot * 0.06;   // 鞋底
    cx = head.x; cz = head.z;
  } else {
    const box = new THREE.Box3().setFromObject(model.scene);
    top = box.max.y; bottom = box.min.y;
  }
  frameCamera.debug = `head=${head && head.toArray().map((v) => v.toFixed(2))} foot=${foot && foot.toArray().map((v) => v.toFixed(2))}`;
  const height = top - bottom;
  const center = new THREE.Vector3(cx, (top + bottom) / 2, cz);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  // 画面上方留出气泡的位置（约 18%），身体放在下面
  const dist = (height / 0.8) / (2 * Math.tan(fov / 2));
  const lift = height * 0.11;
  camera.position.set(center.x, center.y + lift, center.z + dist);
  camera.lookAt(center.x, center.y + lift, center.z);
  frameCamera.center = center;
  frameCamera.height = height;
}

// ---------- 每帧：呼吸、眨眼、看鼠标、表情、说话 ----------
function animate(dt, t) {
  if (simple) { animateSimple(dt, t); return; }
  if (!vrm) return;
  const h = vrm.humanoid;
  // 呼吸和轻微摇摆
  const spine = h.getNormalizedBoneNode('spine');
  const chest = h.getNormalizedBoneNode('chest') || spine;
  if (chest) chest.rotation.x = Math.sin(t * 1.6) * 0.025;
  if (spine) spine.rotation.z = Math.sin(t * 0.7) * 0.02;
  const hips = h.getNormalizedBoneNode('hips');
  if (hips) { // 在原高度上叠加起伏（之前直接赋值成 ~0，整个人沉下去大半截）
    if (animate.hipsY === undefined) animate.hipsY = hips.position.y;
    hips.position.y = animate.hipsY + Math.sin(t * 1.6) * 0.004;
  }

  // 看鼠标：把窗口坐标换成她面前的一点；头转一点、眼睛转多一点
  const c = frameCamera.center || new THREE.Vector3(0, 1, 0);
  const hgt = frameCamera.height || 1.6;
  const nx = THREE.MathUtils.clamp((cursor.x / W) * 2 - 1, -1.6, 1.6);
  const ny = THREE.MathUtils.clamp(1 - (cursor.y / H) * 2, -1.2, 1.2);
  lookTarget.position.set(c.x + nx * hgt * 0.6, c.y + hgt * 0.35 + ny * hgt * 0.4, c.z + hgt * 1.2);
  const head = h.getNormalizedBoneNode('head');
  if (head) {
    head.rotation.y += (THREE.MathUtils.clamp(nx * 0.45, -0.5, 0.5) - head.rotation.y) * Math.min(1, dt * 4);
    head.rotation.x += (THREE.MathUtils.clamp(-ny * 0.25, -0.3, 0.3) - head.rotation.x) * Math.min(1, dt * 4);
  }

  // 表情：朝目标平滑过渡；被戳一下时短暂惊讶
  const target = Object.fromEntries(EXPRESSION_NAMES.map((n) => [n, 0]));
  Object.assign(target, EXPRESSIONS[state.emotion] || {});
  if (t < pokeUntil) Object.assign(target, { surprised: 0.6, happy: 0.3 });
  const em = vrm.expressionManager;
  for (const name of EXPRESSION_NAMES) {
    current[name] += (target[name] - current[name]) * Math.min(1, dt * 6);
    if (em) em.setValue(name, current[name]);
  }

  // 眨眼（困的时候眼睛半闭）
  blinkTimer -= dt;
  if (blinkTimer <= 0) { blinkPhase = 0.18; blinkTimer = 2.5 + Math.random() * 4; }
  let blink = state.emotion === 'sleepy' ? 0.55 : 0;
  if (blinkPhase > 0) { blinkPhase -= dt; blink = Math.max(blink, Math.sin((1 - blinkPhase / 0.18) * Math.PI)); }
  if (em) em.setValue('blink', Math.min(1, blink));

  // 说话：嘴按正弦 + 一点随机开合
  const talk = state.speaking ? THREE.MathUtils.clamp(0.25 + 0.45 * Math.abs(Math.sin(t * 13)) + Math.random() * 0.15, 0, 0.9) : 0;
  if (em) { em.setValue('aa', talk); em.setValue('oh', talk * 0.3); }

  vrm.update(dt); // 头发、尾巴的物理（spring bone）和表情、视线一起更新
}

// ---------- 鼠标：点穿 / 拖动 / 点击 ----------
const raycaster = new THREE.Raycaster();
let overBody = false;
let ignoring = true;

const hitBox = new THREE.Box3();
function hitBody(x, y) {
  raycaster.setFromCamera(new THREE.Vector2((x / W) * 2 - 1, -(y / H) * 2 + 1), camera);
  if (simple) { // 图生 3D 有十几万个面，每秒 30 次逐面检测太重：用缩小一圈的包围盒近似
    hitBox.setFromObject(simple.pivot);
    const shrink = hitBox.getSize(new THREE.Vector3()).multiplyScalar(0.12);
    hitBox.min.add(shrink); hitBox.max.sub(shrink);
    return raycaster.ray.intersectsBox(hitBox);
  }
  return Boolean(vrm) && raycaster.intersectObject(vrm.scene, true).length > 0;
}

dfy.on('cursor', (p) => {
  cursor = { x: p.x, y: p.y, inside: p.x >= 0 && p.y >= 0 && p.x <= p.w && p.y <= p.h };
  overBody = cursor.inside && hitBody(p.x, p.y);
  const shouldIgnore = !overBody && !dragging;
  if (shouldIgnore !== ignoring) { ignoring = shouldIgnore; dfy.send('set-ignore', ignoring); }
});

let dragging = false;
let downAt = null;
window.addEventListener('mousedown', (e) => {
  if (e.button !== 0 || !overBody) return;
  downAt = { x: e.screenX, y: e.screenY, t: performance.now() };
  dragging = true;
  dfy.send('drag-start');
});
window.addEventListener('mouseup', (e) => {
  if (e.button !== 0 || !dragging) return;
  dragging = false;
  dfy.send('drag-end');
  const moved = downAt && Math.hypot(e.screenX - downAt.x, e.screenY - downAt.y) > 4;
  if (!moved) { pokeUntil = clock.elapsedTime + 0.8; dfy.send('pet-event', 'pat'); } // 单击 = 摸头
});
window.addEventListener('dblclick', () => { if (overBody) dfy.send('open-chat'); });
window.addEventListener('contextmenu', (e) => { e.preventDefault(); if (overBody) dfy.send('context-menu'); });

// ---------- 大脑的状态 ----------
dfy.on('pet-state', (s) => {
  if (!s || !s.ok) return;
  state.emotion = s.emotion || 'idle';
  state.speaking = Boolean(s.speaking);
  const text = s.bubble || '';
  if (text !== state.bubble) {
    state.bubble = text;
    bubbleEl.textContent = text;
    bubbleEl.classList.toggle('show', Boolean(text));
  }
});

// ---------- 主循环（空闲 30fps，省显卡）----------
const clock = new THREE.Clock();
let acc = 0;
function loop() {
  requestAnimationFrame(loop);
  const dt = clock.getDelta();
  acc += dt;
  const busy = dragging || state.speaking || t0Interacting();
  if (!busy && acc < 1 / 30) return;
  const step = acc;
  acc = 0;
  animate(Math.min(step, 0.1), clock.elapsedTime);
  renderer.render(scene, camera);
  if (dfy.shot && !loop.shotSent && clock.elapsedTime > 5) { // 刚画完这一帧就导出，不需要 preserveDrawingBuffer
    loop.shotSent = true;
    dfy.send('shot-data', renderer.domElement.toDataURL('image/png'));
  }
}
function t0Interacting() { return clock.elapsedTime < pokeUntil; }

loadVRM().then((model) => {
  vrm = model;
  if (dfy.smoke) { // 冒烟：手动渲染 60 帧，确认模型、表情、物理都跑得动
    try {
      for (let i = 0; i < 60; i += 1) { animate(1 / 30, i / 30); renderer.render(scene, camera); }
      const gl = renderer.getContext();
      const dbg = `model=${simple ? 'glb' : 'vrm'} cam=${camera.position.toArray().map((v) => v.toFixed(2))} center=${frameCamera.center.toArray().map((v) => v.toFixed(2))} h=${frameCamera.height.toFixed(2)} ${frameCamera.debug || ''} inner=${window.innerWidth}x${window.innerHeight} W=${W}x${H} dpr=${window.devicePixelRatio} canvas=${renderer.domElement.width}x${renderer.domElement.height}`;
      dfy.send('smoke-result', { ok: !gl.isContextLost(), msg: gl.isContextLost() ? 'WebGL 上下文丢失' : dbg });
    } catch (err) {
      dfy.send('smoke-result', { ok: false, msg: String(err && err.message || err) });
    }
    return;
  }
  loop();
}).catch((err) => {
  bubbleEl.textContent = `3D 身体没加载出来：${err.message}`;
  bubbleEl.classList.add('show');
  if (dfy.smoke) dfy.send('smoke-result', { ok: false, msg: err.message });
});
