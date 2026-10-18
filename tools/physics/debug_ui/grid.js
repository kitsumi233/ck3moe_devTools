// 十动作并排预览：同一角色、同一来源的全部动作各占一格，共用一个视角同时播放。
// 只读：只调用 /api/catalog、/api/actions、/api/model、/api/animation 四个 GET 接口。
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const element = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const COLUMNS = 5;
const ANCHOR_BONE = 'bn_m_root';
const ACTION_TITLES = { idle: '待机', writing: '书写', sitting: '坐姿', dance_loop: '舞蹈', dance_spin: '转圈', emotion_laugh: '大笑', emotion_shock: '惊讶', emotion_fear: '恐惧', emotion_angry_start: '发怒', jockey_lance_couched_gallop: '骑枪冲锋' };

const state = { request: 0, actors: [], geometries: [], materials: [], playing: true, time: 0, last: performance.now(), catalog: null };

function status(message, error = false) {
  element('status').textContent = message;
  element('status').classList.toggle('error', error);
}

async function api(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

const decode = encoded => Uint8Array.from(atob(encoded), letter => letter.charCodeAt(0)).buffer;
const floats = encoded => new Float32Array(decode(encoded));
const integers = (encoded, Type) => new Type(decode(encoded));

function matrixFromBind(v) {
  return new THREE.Matrix4().set(v[0], v[3], v[6], v[9], v[1], v[4], v[7], v[10], v[2], v[5], v[8], v[11], 0, 0, 0, 1);
}

// ---------------------------------------------------------------- renderer
const stage = element('stage');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setClearColor(0x18212a);
renderer.setScissorTest(true);
stage.prepend(renderer.domElement);
const scene = new THREE.Scene();
scene.add(new THREE.HemisphereLight(0xe5f7ff, 0x626271, 2.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.0);
keyLight.position.set(80, 200, -180);
scene.add(keyLight);
scene.add(new THREE.GridHelper(480, 24, 0x496373, 0x263d4c));
const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 2000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
const textureLoader = new THREE.TextureLoader();
const textureCache = new Map();

function resetCamera() {
  controls.target.set(0, 72, 0);
  camera.position.set(0, 84, -300);
  controls.update();
}
resetCamera();

function cellRects() {
  const width = stage.clientWidth, height = stage.clientHeight;
  const rows = Math.max(1, Math.ceil(state.actors.length / COLUMNS));
  const w = width / COLUMNS, h = height / rows;
  return state.actors.map((_, i) => ({ x: (i % COLUMNS) * w, y: height - (Math.floor(i / COLUMNS) + 1) * h, w, h }));
}

new ResizeObserver(() => renderer.setSize(stage.clientWidth, stage.clientHeight)).observe(stage);

// ---------------------------------------------------------------- model
// Geometry and materials are built once per model and shared by all ten actors;
// each actor only owns its own bone hierarchy and skeleton.
function buildShared(model) {
  const parts = {};
  for (const [partName, part] of Object.entries(model.parts)) {
    const meshes = [];
    for (const data of part.meshes) {
      if (data.hidden) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(floats(data.positions), 3));
      geometry.setIndex(new THREE.BufferAttribute(integers(data.triangles, Uint32Array), 1));
      if (data.normals) geometry.setAttribute('normal', new THREE.BufferAttribute(floats(data.normals), 3));
      else geometry.computeVertexNormals();
      if (data.uv) {
        const uv = floats(data.uv);
        for (let i = 1; i < uv.length; i += 2) uv[i] = 1 - uv[i];
        geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
      }
      const skinned = Boolean(data.indices);
      if (skinned) {
        geometry.setAttribute('skinIndex', new THREE.BufferAttribute(integers(data.indices, Uint16Array), 4));
        geometry.setAttribute('skinWeight', new THREE.BufferAttribute(floats(data.weights), 4));
      }
      let texture = null;
      if (data.texture) {
        if (!textureCache.has(data.texture)) {
          const loaded = textureLoader.load(data.texture, undefined, undefined, () => status(`贴图加载失败：${data.texture}`, true));
          loaded.colorSpace = THREE.SRGBColorSpace;
          textureCache.set(data.texture, loaded);
        }
        texture = textureCache.get(data.texture);
      }
      const material = new THREE.MeshStandardMaterial({ map: texture, color: texture ? 0xffffff : 0xc8d2d9, roughness: 0.92, metalness: 0, side: THREE.DoubleSide, alphaTest: data.alpha_test });
      state.geometries.push(geometry); state.materials.push(material);
      meshes.push({ geometry, material, skinned });
    }
    parts[partName] = { bones: part.bones, meshes };
  }
  return parts;
}

function buildActor(shared) {
  const root = new THREE.Group();
  const actor = { root, parts: {}, skeletons: [] };
  for (const [partName, part] of Object.entries(shared)) {
    const bones = part.bones.map(info => { const bone = new THREE.Bone(); bone.name = info.name; return bone; });
    const inverse = part.bones.map(info => matrixFromBind(info.inverse));
    for (const [i, info] of part.bones.entries()) {
      const local = inverse[i].clone().invert();
      if (info.parent >= 0) local.premultiply(inverse[info.parent]);
      local.decompose(bones[i].position, bones[i].quaternion, bones[i].scale);
      (info.parent >= 0 ? bones[info.parent] : root).add(bones[i]);
    }
    root.updateMatrixWorld(true);
    const skeleton = new THREE.Skeleton(bones, inverse);
    actor.skeletons.push(skeleton);
    const byName = new Map(bones.map(bone => [bone.name, bone]));
    actor.parts[partName] = { byName, anchor: byName.get(ANCHOR_BONE) || null };
    for (const { geometry, material, skinned } of part.meshes) {
      let mesh;
      if (skinned) { mesh = new THREE.SkinnedMesh(geometry, material); mesh.bind(skeleton, new THREE.Matrix4()); }
      else mesh = new THREE.Mesh(geometry, material);
      mesh.frustumCulled = false;
      root.add(mesh);
    }
  }
  root.visible = false;
  scene.add(root);
  return actor;
}

function disposeAll() {
  for (const actor of state.actors) {
    scene.remove(actor.root);
    for (const skeleton of actor.skeletons) skeleton.dispose();
  }
  for (const g of state.geometries) g.dispose();
  for (const m of state.materials) m.dispose();
  state.actors = []; state.geometries = []; state.materials = [];
  element('cells').replaceChildren();
}

function decodeAnimation(data) {
  for (const part of Object.values(data)) {
    part.channels = part.channels.map(c => ({ name: c.name, translation: floats(c.translation), quaternion: floats(c.quaternion) }));
  }
  return data;
}

// ---------------------------------------------------------------- pose
const nextPosition = new THREE.Vector3();
const nextQuaternion = new THREE.Quaternion();
const rootInverse = new THREE.Matrix4();

// Same head/body alignment as the single-action viewer: head skeleton follows the
// body-side shared anchor bone, otherwise the head stays behind when body_root moves.
function attachHeadToBody(actor) {
  const head = actor.parts.head, body = actor.parts.body;
  if (!head?.anchor || !body?.anchor) return;
  rootInverse.copy(actor.root.matrixWorld).invert();
  head.anchor.matrix.multiplyMatrices(rootInverse, body.anchor.matrixWorld);
  head.anchor.matrix.decompose(head.anchor.position, head.anchor.quaternion, head.anchor.scale);
}

function pose(actor) {
  const body = actor.animation.body;
  // Each action loops on its own clock: frame count and fps differ per action.
  const sample = (state.time * body.fps) % Math.max(1, body.frames - 1);
  actor.frame = Math.floor(sample);
  for (const [partName, data] of Object.entries(actor.animation)) {
    const s = Math.min(data.frames - 1, sample);
    const frame = Math.floor(s), next = Math.min(data.frames - 1, frame + 1), fraction = s - frame;
    for (const channel of data.channels) {
      const bone = actor.parts[partName]?.byName.get(channel.name);
      if (!bone) continue;
      bone.position.fromArray(channel.translation, channel.translation.length > 3 ? frame * 3 : 0);
      bone.quaternion.fromArray(channel.quaternion, channel.quaternion.length > 4 ? frame * 4 : 0).normalize();
      if (fraction > 0 && next !== frame) {
        if (channel.translation.length > 3) bone.position.lerp(nextPosition.fromArray(channel.translation, next * 3), fraction);
        if (channel.quaternion.length > 4) bone.quaternion.slerp(nextQuaternion.fromArray(channel.quaternion, next * 4).normalize(), fraction);
      }
    }
  }
  actor.root.updateMatrixWorld(true);
  attachHeadToBody(actor);
  actor.root.updateMatrixWorld(true);
}

function render(now) {
  requestAnimationFrame(render);
  const dt = Math.min(0.1, (now - state.last) / 1000);
  state.last = now;
  if (state.playing) state.time += dt * Number(element('speed').value);
  controls.update();
  const rects = cellRects();
  for (const [i, actor] of state.actors.entries()) {
    if (!actor.animation) continue;
    pose(actor);
    const r = rects[i];
    camera.aspect = r.w / r.h;
    camera.updateProjectionMatrix();
    renderer.setViewport(r.x, r.y, r.w, r.h);
    renderer.setScissor(r.x, r.y, r.w, r.h);
    actor.root.visible = true;
    renderer.render(scene, camera);
    actor.root.visible = false;
    if (actor.label) actor.label.textContent = `${actor.frame} / ${actor.animation.body.frames - 1}`;
  }
}
requestAnimationFrame(render);

// ---------------------------------------------------------------- loading
function syncUrl() {
  const url = new URL(location.href);
  url.searchParams.set('character', element('character').value);
  url.searchParams.set('source', element('source').value);
  history.replaceState(null, '', url);
  element('single').href = `/?character=${encodeURIComponent(element('character').value)}&source=${encodeURIComponent(element('source').value)}`;
}

function populateCharacters(keep) {
  const source = state.catalog.sources.find(s => s.id === element('source').value);
  const names = new Map(state.catalog.characters.map(c => [c.id, c.name]));
  const allowed = source?.characters || [];
  element('character').replaceChildren(...allowed.map(ck => new Option(`${names.get(ck) || ck} · ${ck}`, ck)));
  element('character').value = allowed.includes(keep) ? keep : allowed[0] || '';
}

async function loadGrid() {
  const request = ++state.request;
  const character = element('character').value, source = element('source').value;
  syncUrl();
  disposeAll();
  if (!character) { status('该来源没有可预览的角色', true); return; }
  status('正在读取模型与动作列表…');
  const [model, actions] = await Promise.all([
    api(`/api/model?${new URLSearchParams({ character, source })}`),
    api(`/api/actions?${new URLSearchParams({ character, source })}`),
  ]);
  if (request !== state.request) return;
  const ids = actions.filter(a => a.available).map(a => a.id);
  const order = Object.keys(ACTION_TITLES);
  ids.sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
  if (!ids.length) { status('该角色在此来源没有可预览的动作', true); return; }
  const shared = buildShared(model);
  const cells = element('cells');
  cells.style.gridTemplateRows = `repeat(${Math.ceil(ids.length / COLUMNS)}, 1fr)`;
  for (const id of ids) {
    const actor = buildActor(shared);
    actor.action = id;
    const cell = document.createElement('div'); cell.className = 'cell';
    const tag = document.createElement('span'); tag.textContent = ACTION_TITLES[id] || id;
    tag.title = id;
    actor.label = document.createElement('em'); tag.append(actor.label);
    cell.append(tag); cells.append(cell);
    state.actors.push(actor);
  }
  status(`正在读取 ${ids.length} 个动作的动画…`);
  let done = 0;
  await Promise.all(state.actors.map(async actor => {
    const data = await api(`/api/animation?${new URLSearchParams({ character, action: actor.action, source })}`);
    if (request !== state.request) return;
    actor.animation = decodeAnimation(data);
    // Mounted actions (jockey_*) lift the rider far above the ground and out of the
    // shared camera. Drop each cell once so its lowest body bone at frame 0 touches y=0;
    // standing and sitting clips already have their feet near the ground, so they barely move.
    const saved = state.time; state.time = 0;
    pose(actor);
    state.time = saved;
    // Anchor on the ankles: ground_joint/body_root stay at the origin even when the
    // rider is lifted, so "lowest bone" would never move the cell.
    let lowest = Infinity;
    const p = new THREE.Vector3();
    for (const name of ['bn_m_l_ankle', 'bn_m_r_ankle']) {
      const bone = actor.parts.body.byName.get(name);
      if (bone) lowest = Math.min(lowest, bone.getWorldPosition(p).y);
    }
    if (Number.isFinite(lowest) && lowest > 20) actor.root.position.y = -(lowest - 8);
    done += 1;
    status(`已载入 ${done} / ${ids.length} 个动作`);
  }));
  if (request !== state.request) return;
  state.time = 0;
  status(`${model.source_label || source} · ${ids.length} 个动作同步播放`);
}

async function init() {
  state.catalog = await api('/api/catalog');
  const sources = state.catalog.sources.filter(s => s.characters?.length);
  element('source').replaceChildren(...sources.map(s => new Option(s.label || s.id, s.id)));
  const wanted = params.get('source');
  element('source').value = sources.some(s => s.id === wanted) ? wanted : (sources.find(s => s.id === 'gate_main_10') || sources[0]).id;
  populateCharacters(params.get('character'));
  element('source').addEventListener('change', () => { populateCharacters(element('character').value); loadGrid().catch(handleError); });
  element('character').addEventListener('change', () => loadGrid().catch(handleError));
  element('play').addEventListener('click', () => { state.playing = !state.playing; element('play').textContent = state.playing ? '暂停' : '播放'; });
  element('restart').addEventListener('click', () => { state.time = 0; });
  element('reset-camera').addEventListener('click', resetCamera);
  await loadGrid();
}

function handleError(error) {
  console.error(error);
  status(error.message || String(error), true);
}

init().catch(handleError);
