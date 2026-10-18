import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const element = id => document.getElementById(id);
const state = { model: null, profile: null, actors: [], frame: 0, playing: false, selected: null, request: 0, run: null, evidenceSource: null, job: null, busy: false, loading: false, readOnly: false, frameCount: 1, fps: 30 };
const EXPRESSION_SOURCE = 'expression_v2';
const SAMPLE_SOURCE = 'sample_review_20260919';
// The 92-character spring/damping trial is a preview provider, just like the
// older expression and sample providers.  Keeping it out of this set makes a
// URL such as `?source=response92_v1` fall back to the very slow `current`
// action scan, so selecting a character appears to hang before a model is
// ever requested.
const RESPONSE_SOURCE = 'response92_v1';
const LAU_RESPONSE_SOURCE = 'lau_all_spring_skirt_damped10';
const TWIST_Z_SOURCE = 'twist_z30_5roles10';
const TWIST_Z_LOCK_SOURCE = 'twist_z0_5roles10';
const BONE_TWIST_LOCK_SOURCE = 'bone_local_z_lock_5roles10';
const PARTIAL_BONE_TWIST_SOURCE = 'bone_local_z_lock_partial92';
const GATE_CANDIDATE_SOURCE = 'gate_ndsw_5roles10';
const GATE_CONTROL_SOURCE = 'gate_p_5roles10';
const GATE_C60_SOURCE = 'gate_c60_5roles10';
const GATE_C60DX_SOURCE = 'gate_c60dx_2roles10';
const GATE_C60DXV_SOURCE = 'gate_c60dxv_2roles10';
const GATE_C60DXG_SOURCE = 'gate_c60dxg_2roles10';
const GATE_C60DXGL_SOURCE = 'gate_c60dxgl_2roles10';
const GATE_FULL_SOURCE = 'gate_full_5roles10';
const GATE_ALL92_SOURCE = 'gate_all92_10';
const GATE_OLD92_SOURCE = 'gate_old92_10';
const GATE_ALL92H_SOURCE = 'gate_all92h_10';
const GATE_ALL92R_SOURCE = 'gate_all92r_10';
const GATE_FINAL_SOURCE = 'gate_final_10';
const GATE_PALM_SOURCE = 'gate_palm_10';
const GATE_FX_SOURCE = 'gate_fx_10';
const GATE_ALLFIX_SOURCE = 'gate_allfix_10';
const GATE_SOFT1_SOURCE = 'gate_soft1_10';
const GATE_MAIN_SOURCE = 'gate_main_10';
const GATE_CLOUD_SOURCE = 'gate_cloud_review';
const LAU_SITTING_SPRING_SOURCE = 'lau_sitting_spring_v1';
const LAU_SITTING_DYNAMIC_SOURCE = 'lau_sitting_dynamic_v1';
const reviewMode = new URLSearchParams(location.search).get('review') === '1';
const JIA_FREE_HAIR_SOURCE = 'jia_freehair_gentle';
const previewSources = new Set([EXPRESSION_SOURCE, SAMPLE_SOURCE, RESPONSE_SOURCE, LAU_RESPONSE_SOURCE, TWIST_Z_SOURCE, TWIST_Z_LOCK_SOURCE, BONE_TWIST_LOCK_SOURCE, PARTIAL_BONE_TWIST_SOURCE, GATE_CANDIDATE_SOURCE, GATE_CONTROL_SOURCE, GATE_C60_SOURCE, GATE_C60DX_SOURCE, GATE_C60DXV_SOURCE, GATE_C60DXG_SOURCE, GATE_C60DXGL_SOURCE, GATE_FULL_SOURCE, GATE_ALL92_SOURCE, GATE_ALL92H_SOURCE, GATE_ALL92R_SOURCE, GATE_FINAL_SOURCE, GATE_PALM_SOURCE, GATE_FX_SOURCE, GATE_ALLFIX_SOURCE, GATE_SOFT1_SOURCE, GATE_MAIN_SOURCE, GATE_OLD92_SOURCE, LAU_SITTING_SPRING_SOURCE, LAU_SITTING_DYNAMIC_SOURCE, JIA_FREE_HAIR_SOURCE,
  'jia_freehair_forces10', 'jia_sourceangles_forces10', 'jia_internalhair_forces10',
  'jia_spring_narrow10', 'jia_spring_hair03_sitting07510', 'jia_spring_hair100_sitting07510',
  'jia_spring_cloth10', 'jia_dance_spin_hair12', GATE_CLOUD_SOURCE]);
let serverReadOnly = false;
let onlySource = null;
const sourceLabels = { current: '当前正式物理', base: '基础动作', trial: '本次试烘', expression_v2: '新版表情 · 已校验的物理候选' };
sourceLabels[LAU_RESPONSE_SOURCE] = '洛茜全动态弹簧／阻尼调优 · 10动作 · 待验候选';
sourceLabels[TWIST_Z_SOURCE] = '局部 Z 自转 ±30° 试验 · 5角色10动作 · 有超限／回归，未接受';
sourceLabels[TWIST_Z_LOCK_SOURCE] = '旧版关节 Z 锁定失败 · 仅供失败对照 · 五角色十动作';
sourceLabels[BONE_TWIST_LOCK_SOURCE] = '物理骨骼纵向 Z 单自由度零锁 · 洛茜、陈千语、梨诺、庄方宜、提弗洛斯 · 待验候选';
sourceLabels[PARTIAL_BONE_TWIST_SOURCE] = '物理骨骼纵向局部 Z 单自由度锁定 · 已完成角色逐动作审核 · 弹簧与阻尼候选';
sourceLabels[GATE_CANDIDATE_SOURCE] = '快速门选定候选 · 原生锁+写回去自转，裙/飘带不锁，头发碰背墙 · 五角色十动作 · 待验';
sourceLabels[GATE_CONTROL_SOURCE] = '快速门对照 · 现行做法（锁+每步硬扳） · 五角色十动作';
sourceLabels[GATE_C60_SOURCE] = '快速门候选+软接触 60Hz · 五角色十动作';
sourceLabels[GATE_C60DX_SOURCE] = '软接触60Hz+重阻尼+提灯不碰裙 · 陈千语/提弗洛斯';
sourceLabels[GATE_C60DXV_SOURCE] = '同上+慢推开(1) · 陈千语/提弗洛斯';
sourceLabels[GATE_C60DXG_SOURCE] = '同上+按穿透深度推开 · 陈千语/提弗洛斯';
sourceLabels[GATE_C60DXGL_SOURCE] = '按深度推开+写回低通1.5Hz · 陈千语/提弗洛斯';
sourceLabels[GATE_FULL_SOURCE] = '定案候选 · 五角色十动作';
sourceLabels[GATE_ALL92_SOURCE] = '定案设置 · 92 角色试烘';
sourceLabels[GATE_ALL92H_SOURCE] = '定案设置 + 头发弹簧 20Hz · 92 角色';
sourceLabels[GATE_ALL92R_SOURCE] = '头发20Hz+飘带锁+耳饰8Hz · 92 角色';
sourceLabels[GATE_FINAL_SOURCE] = '修复版 · 92 角色';
sourceLabels[GATE_PALM_SOURCE] = '手掌修正版 · 4 角色';
sourceLabels[GATE_FX_SOURCE] = '表情修正版 · 28 角色';
sourceLabels[GATE_ALLFIX_SOURCE] = '全角色合并版 · 92 角色';
sourceLabels[GATE_SOFT1_SOURCE] = '终末地变软试验 · 8 角色';
sourceLabels[GATE_MAIN_SOURCE] = '主版 · 92 角色';
sourceLabels[GATE_CLOUD_SOURCE] = '云端重烘 · gate 超基准 9 角色（15fps）';
sourceLabels[GATE_OLD92_SOURCE] = '旧版 92 角色试烘（09-26）';
sourceLabels[LAU_SITTING_SPRING_SOURCE] = '洛茜坐姿 · 弹簧阻尼基线 · 无额外风力 · 待审候选';
sourceLabels[LAU_SITTING_DYNAMIC_SOURCE] = '洛茜坐姿 · 低频水平风动态候选 · 裙腿交叉风险，待直接预览';
sourceLabels.jia_freehair_gentle = '绛雨旧试验 · 全自由导致上飘 · 已拒收';
sourceLabels.jia_freehair_forces10 = '绛雨诊断对照 · 10动作 · 全自由＋外力顺序修正';
sourceLabels.jia_sourceangles_forces10 = '绛雨快速迭代 · 10动作 · 原始关节＋外力顺序修正';
sourceLabels.jia_internalhair_forces10 = '绛雨快速迭代 · 10动作 · 无弹簧消融对照（头发效果差）';
sourceLabels.jia_spring_narrow10 = '绛雨快速迭代 · 10动作 · 回中弹簧候选（坐姿裙高频待修）';
sourceLabels.jia_spring_hair03_sitting07510 = '绛雨快速迭代 · 头发弹簧0.3＋坐姿裙阻尼0.75（10动作候选）';
sourceLabels.jia_spring_hair100_sitting07510 = '绛雨快速迭代 · 头发弹簧100＋坐姿裙阻尼0.75（10动作候选，只读）';
sourceLabels.jia_spring_cloth10 = '绛雨诊断 · 10动作 · 衣带长链放开（已拒收）';
sourceLabels.jia_dance_spin_hair12 = '绛雨动作特调 · dance_spin · 头发回中 1.2（只读候选）';
const token = document.querySelector('meta[name="moe-token"]').content;
const viewport = element('viewport');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setClearColor(0x18212a);
viewport.prepend(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(37, 1, 0.1, 2000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xe5f7ff, 0x626271, 2.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.0);
keyLight.position.set(80, 200, -180);
scene.add(keyLight);
scene.add(new THREE.GridHelper(480, 24, 0x496373, 0x263d4c));
const textureLoader = new THREE.TextureLoader();
const textureCache = new Map();
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

function status(message, error = false) {
  element('status').textContent = message;
  element('status').style.color = error ? '#ffac9c' : '';
}

async function api(path, body) {
  const response = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Moe-Debug': token }, body: JSON.stringify(body) } : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function floats(encoded) {
  return new Float32Array(Uint8Array.from(atob(encoded), letter => letter.charCodeAt(0)).buffer);
}

function integers(encoded, Type) {
  return new Type(Uint8Array.from(atob(encoded), letter => letter.charCodeAt(0)).buffer);
}

function matrixFromBind(values) {
  return new THREE.Matrix4().set(values[0], values[3], values[6], values[9], values[1], values[4], values[7], values[10], values[2], values[5], values[8], values[11], 0, 0, 0, 1);
}

// head 与 body 都存在的最高共享骨。body 侧它挂在 body_root 下、局部 t 抵消了
// body_root 的抬升；head 侧它就是根。锚在这根上，两套骨架的世界变换才能对齐。
const ANCHOR_BONE = 'bn_m_root';

// 把 head 骨架对齐到 body 侧同名锚骨的世界变换。
// body_root 带 242 帧通道，动画期间会搬走整条身体链，而 head 那套没有这根骨 ——
// 不补这一步，头就留在原地，看起来像头身错位（产物其实是对的）。
function attachHeadToBody(actor) {
  const head = actor.parts.head, body = actor.parts.body;
  if (!head || !body || !head.anchor || !body.anchor) return;
  // head.anchor 是它那套骨架的根，父就是 actor.root，所以要赋的是**局部**矩阵。
  // body.anchor.matrixWorld 是**世界**矩阵 —— 两者只在 actor.root 为单位矩阵时相等,
  // 而对比模式会写 actor.root.position.x = ±65（见 updatePose）。直接赋值会让头部
  // 多吃一次那个偏移、横向飞出 65 单位，且只在勾了对比时才出现。
  // 所以先左乘 root 世界矩阵的逆，把它降回 root 空间。
  const rootInverse = new THREE.Matrix4().copy(actor.root.matrixWorld).invert();
  head.anchor.matrix.multiplyMatrices(rootInverse, body.anchor.matrixWorld);
  head.anchor.matrix.decompose(head.anchor.position, head.anchor.quaternion, head.anchor.scale);
}

function buildActor(model) {
  const root = new THREE.Group();
  const actor = { root, parts: {}, colliders: [], meshes: [] };
  for (const [partName, part] of Object.entries(model.parts)) {
    const bones = part.bones.map(info => { const bone = new THREE.Bone(); bone.name = info.name; return bone; });
    const inverse = part.bones.map(info => matrixFromBind(info.inverse));
    const bindWorld = inverse.map(matrix => matrix.clone().invert());
    for (const [index, info] of part.bones.entries()) {
      const local = bindWorld[index].clone();
      if (info.parent >= 0) local.premultiply(inverse[info.parent]);
      local.decompose(bones[index].position, bones[index].quaternion, bones[index].scale);
      (info.parent >= 0 ? bones[info.parent] : root).add(bones[index]);
    }
    root.updateMatrixWorld(true);
    const skeleton = new THREE.Skeleton(bones, inverse);
    actor.parts[partName] = { bones, skeleton, byName: new Map(bones.map(bone => [bone.name, bone])) };
    // 两件 mesh 的根链结构不同（body 多 ground_joint / body_root 两根摆位骨），
    // head 侧缺 body_root 那 242 帧的搬运量，所以要记下共享锚骨供每帧对齐。
    actor.parts[partName].anchor = actor.parts[partName].byName.get(ANCHOR_BONE) || null;
    for (const data of part.meshes) {
      if (data.hidden) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(floats(data.positions), 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(data.count * 3).fill(1), 3));
      geometry.setIndex(new THREE.BufferAttribute(integers(data.triangles, Uint32Array), 1));
      if (data.normals) geometry.setAttribute('normal', new THREE.BufferAttribute(floats(data.normals), 3));
      else geometry.computeVertexNormals();
      if (data.uv) {
        const uv = floats(data.uv);
        for (let index = 1; index < uv.length; index += 2) uv[index] = 1 - uv[index];
        geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
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
      const material = new THREE.MeshStandardMaterial({ map: texture, color: texture ? 0xffffff : 0xc8d2d9, roughness: 0.92, metalness: 0, side: THREE.DoubleSide, alphaTest: data.alpha_test, vertexColors: true });
      let mesh;
      if (data.indices) {
        geometry.setAttribute('skinIndex', new THREE.BufferAttribute(integers(data.indices, Uint16Array), 4));
        geometry.setAttribute('skinWeight', new THREE.BufferAttribute(floats(data.weights), 4));
        mesh = new THREE.SkinnedMesh(geometry, material);
        mesh.bind(skeleton, new THREE.Matrix4());
      } else mesh = new THREE.Mesh(geometry, material);
      mesh.frustumCulled = false;
      mesh.userData.part = partName;
      // submesh 名就是它自己的身份，hiddenMeshes 直接按它判断
      mesh.userData.material = data.material || '';
      root.add(mesh);
      actor.meshes.push(mesh);
    }
  }
  for (const info of model.rigids) {
    if (!info.preview_valid) continue;
    let geometry;
    if (info.type === 0) geometry = new THREE.SphereGeometry(info.size[0], 8, 6);
    else if (info.type === 1) geometry = new THREE.BoxGeometry(...info.size.map(value => value * 2));
    else geometry = new THREE.CapsuleGeometry(info.size[0], info.size[1], 4, 8);
    const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: info.mode ? 0x63c8d2 : 0xa68dd7, wireframe: true, transparent: true, opacity: 0.3, depthTest: false }));
    mesh.matrixAutoUpdate = false;
    mesh.userData.rigid = info;
    mesh.renderOrder = 10;
    scene.add(mesh);
    actor.colliders.push(mesh);
  }
  scene.add(root);
  return actor;
}

function disposeActors() {
  for (const actor of state.actors) {
    scene.remove(actor.root);
    for (const mesh of [...actor.meshes, ...actor.colliders]) {
      scene.remove(mesh);
      mesh.geometry.dispose();
      mesh.material.dispose();
    }
    for (const part of Object.values(actor.parts)) part.skeleton.dispose();
  }
  state.actors = [];
}

function decodeAnimation(data) {
  for (const part of Object.values(data)) {
    part.channels = part.channels.map(channel => ({ name: channel.name, translation: floats(channel.translation), quaternion: floats(channel.quaternion) }));
  }
  return data;
}

const interpolatedNextPosition = new THREE.Vector3();
const interpolatedNextQuaternion = new THREE.Quaternion();

function updatePose() {
  for (const [actorIndex, actor] of state.actors.entries()) {
    actor.root.visible = actorIndex === (reviewMode ? 1 : 0) || element('compare').checked;
    actor.root.position.x = element('compare').checked ? (actorIndex === 0 ? 65 : -65) : 0;
    if (!actor.animation) continue;
    for (const [partName, data] of Object.entries(actor.animation)) {
      const sample = Math.min(data.frames - 1, state.frame / Math.max(1, state.frameCount - 1) * (data.frames - 1));
      const frame = Math.floor(sample);
      const nextFrame = Math.min(data.frames - 1, frame + 1);
      const fraction = sample - frame;
      for (const channel of data.channels) {
        const bone = actor.parts[partName].byName.get(channel.name);
        if (!bone) continue;
        bone.position.fromArray(channel.translation, channel.translation.length > 3 ? frame * 3 : 0);
        bone.quaternion.fromArray(channel.quaternion, channel.quaternion.length > 4 ? frame * 4 : 0).normalize();
        if (fraction > 0 && nextFrame !== frame) {
          if (channel.translation.length > 3) {
            interpolatedNextPosition.fromArray(channel.translation, nextFrame * 3);
            bone.position.lerp(interpolatedNextPosition, fraction);
          }
          if (channel.quaternion.length > 4) {
            interpolatedNextQuaternion.fromArray(channel.quaternion, nextFrame * 4).normalize();
            bone.quaternion.slerp(interpolatedNextQuaternion, fraction);
          }
        }
      }
    }
    actor.root.updateMatrixWorld(true);
    // 通道已写完、碰撞盒还没读 matrixWorld —— 对齐必须夹在这中间，
    // 早了会被 .anim 覆盖，晚了碰撞盒会用没对齐的头部骨。
    attachHeadToBody(actor);
    actor.root.updateMatrixWorld(true);
    for (const mesh of actor.colliders) {
      const info = mesh.userData.rigid;
      const bone = actor.parts[info.part].byName.get(info.bone);
      mesh.matrix.fromArray(info.offset);
      mesh.matrix.premultiply(bone ? bone.matrixWorld : actor.root.matrixWorld);
      const size = actorIndex === 1 ? state.profile.rigids[info.name]?.size_scale ?? 1 : 1;
      mesh.matrix.scale(new THREE.Vector3(size, size, size));
      const selected = info.group === state.selected;
      // 退出解算的部件不画碰撞盒 —— 还画着会让人以为它仍在参与物理。
      // 右侧（试烘结果）才反映 profile；左侧是当前物理，照原样画。
      mesh.visible = actor.root.visible && element('colliders').checked
        && !hiddenColliders.has(info.group)
        // 关掉碰撞的组在试烘侧本就不挡东西，画着盒子会让人以为它还在挡
        && !(actorIndex === 1 && isNonColliding(info.group))
        && !(actorIndex === 1 && ['follow', 'no_collision'].includes(state.profile.rigids[info.name]?.mode));
      mesh.material.color.set(selected ? 0xffcd76 : info.mode ? 0x63c8d2 : 0xa68dd7);
      mesh.material.opacity = selected ? 0.4 : 0.12;
    }
    for (const mesh of actor.meshes) {
      mesh.material.wireframe = element('wireframe').checked;
      mesh.visible = actor.root.visible && !hiddenMeshes.has(mesh.userData.material);
    }
  }
  element('frame').value = Math.floor(state.frame);
  if (document.activeElement !== element('jump-frame')) element('jump-frame').value = Math.floor(state.frame);
  element('frame-label').textContent = `${Math.floor(state.frame)} / ${state.frameCount - 1}`;
}

function resetCamera() {
  controls.target.set(0, 75, 0);
  camera.position.set(0, 88, -320);
  controls.update();
}

new ResizeObserver(() => {
  const width = viewport.clientWidth, height = viewport.clientHeight;
  renderer.setSize(width, height);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}).observe(viewport);
resetCamera();

const partSettings = [
  ['mass_scale', '质量倍率', 0.1, 5, 0.1, 1],
  ['size_scale', '碰撞体尺寸倍率', 0.1, 2, 0.05, 1],
  ['linear_damping', '移动阻尼', 0, 1, 0.01, null],
  ['angular_damping', '旋转阻尼', 0, 1, 0.01, null],
  ['limit_scale', '关节活动范围倍率', 0, 2, 0.05, 1],
  ['spring_scale', '弹簧强度倍率', 0, 3, 0.1, 1],
];
const solverSettings = [
  ['drive_gain', '身体驱动倍率', 1, 12, 0.5], ['iters', '求解迭代次数', 10, 120, 10],
  ['hz', '解算频率', 60, 480, 60], ['erp_limit', '限位纠正强度', 0, 0.2, 0.005],
  ['erp2', '碰撞纠正强度', 0, 0.5, 0.01], ['gravity', '重力', -196, 0, 1], ['warmup', '预热圈数', 1, 5, 1],
];

function slider(parent, setting, onChange) {
  const [key, title, min, max, step] = setting;
  const label = document.createElement('label');
  label.className = 'slider-label';
  const row = document.createElement('div');
  const caption = document.createElement('span');
  caption.textContent = title;
  const number = document.createElement('input');
  const range = document.createElement('input');
  number.type = 'number'; range.type = 'range';
  for (const input of [number, range]) {
    input.min = min; input.max = max; input.step = step;
    input.setAttribute('aria-label', title + (input === number ? '数值' : ''));
    input.dataset.key = key;
    input.addEventListener('change', () => {
      const value = Number(input.value);
      if (!Number.isFinite(value) || value < min || value > max) return;
      number.value = value; range.value = value;
      onChange(key, value);
    });
  }
  row.append(caption, number); label.append(row, range); parent.append(label);
}

function groupLabel(name) {
  const special = { Skirt: '裙子', SleeveL: '左袖', SleeveR: '右袖', CapeA: '袖布', ArmRCard: '臂饰', HandLLace: '左手蕾丝', HandRLace: '右手蕾丝' };
  if (special[name]) return special[name];
  if (name.startsWith('Hair')) return `头发 ${name.slice(4)}`;
  if (/^(Ribbon|Cape|Tie|Tassel)/.test(name)) return `飘带 ${name}`;
  if (name.startsWith('Earring')) return `耳饰 ${name}`;
  if (name.startsWith('ShoeLace')) return `鞋饰 ${name}`;
  return name;
}

// 身体碰撞墙的骨名。这些是裙子与头发的支撑，摘掉裙子会直接穿过身体
// （实测只留裙子且不碰墙：max 24.43 → 115.04），所以锁死不让关。
// 判定按骨名而不是「组里是否全是墙」—— 后者对混合组必然判错：
// Lf_cloth_loft 是 5 墙 + 3 动态，但那 5 面墙是布片自己的碰撞板，不是身体支撑。
const BODY_WALL = /^(首|頭|左肩|右肩|左腕|右腕|左ひじ|右ひじ|左手首|右手首|左手捩|右手捩|左腕捩|右腕捩|上半身|下半身|左足|右足|左ひざ|右ひざ|左足首|右足首)/;

function isBodyWall(rigid) {
  return rigid.mode === 0 && BODY_WALL.test(rigid.name);
}

// 这一组有没有能被关掉的刚体：动态体，或挂件自带的碰撞板（非身体墙的 mode0）。
function hasDynamic(group) {
  return state.model
    ? state.model.rigids.some(rigid => rigid.group === group && !rigid.follow && !isBodyWall(rigid))
    : false;
}

// 这一组是否已被设为退出解算。以 profile 为准，不另存状态 ——
// 载入试烘参数时（loadTrial 会整份替换 state.profile）勾选框要跟着变。
function isFollowed(group) {
  // 可关的那些（动态体 + 挂件自带碰撞板）是否**全部**已关。混合组里两类刚体
  // 用不同的 mode：动态体走 follow，墙走 no_collision，所以两者都要认。
  const closeable = state.model
    ? state.model.rigids.filter(rigid => rigid.group === group && !isBodyWall(rigid))
    : [];
  return closeable.length > 0 && closeable.every(rigid => {
    const mode = state.profile.rigids[rigid.name]?.mode;
    return mode === 'follow' || mode === 'no_collision';
  });
}

// 这一组是否已被设为不挡东西。以 profile 为准（loadTrial 会整份替换 profile）。
// follow 也算 —— 它连掩码一起归零了，比 no_collision 更彻底。
function isNonColliding(group) {
  const rigids = state.model
    ? state.model.rigids.filter(rigid => rigid.group === group)
    : [];
  return rigids.length > 0 && rigids.every(rigid => {
    const mode = state.profile.rigids[rigid.name]?.mode;
    return mode === 'no_collision' || mode === 'follow';
  });
}

// 让整组不挡/重新挡东西。no_collision 只把 collision_group_mask 清零
// （physics_profile.py:178），刚体仍参与解算、仍写回骨骼 —— 与 follow 的区别是
// 「还在，但不挡了」。对身体墙这类运动学体，这是唯一有意义的「关掉」方式。
function setPartCollide(group, collide) {
  if (state.readOnly || state.loading || state.busy || !state.model) return;
  for (const rigid of state.model.rigids) {
    if (rigid.group !== group) continue;
    // follow 比 no_collision 更彻底，别把它降级回来
    if (!collide && state.profile.rigids[rigid.name]?.mode === 'follow') continue;
    if (!collide) {
      state.profile.rigids[rigid.name] ||= {};
      state.profile.rigids[rigid.name].mode = 'no_collision';
    } else if (state.profile.rigids[rigid.name]?.mode === 'no_collision') {
      delete state.profile.rigids[rigid.name].mode;
      if (!Object.keys(state.profile.rigids[rigid.name]).length) delete state.profile.rigids[rigid.name];
    }
  }
  status(collide ? `${groupLabel(group)} 已恢复碰撞，点击“试烘当前动作”看效果`
                 : `${groupLabel(group)} 已不挡任何东西（仍参与解算），点击“试烘当前动作”看效果`);
}

// 让整组退出/回到解算。follow 与 moe_bullet_bake 的 follow_rigids 一致：
// 掩码归零、关节摘除、变成纯跟随动画骨的运动学体 —— 等价于「这个部件不存在」。
function setPartFollow(group, follow) {
  if (state.readOnly || state.loading || state.busy || !state.model) return;
  for (const rigid of state.model.rigids) {
    if (rigid.group !== group) continue;
    // 身体墙永不动 —— 它是裙子的支撑
    if (isBodyWall(rigid)) continue;
    if (follow) {
      state.profile.rigids[rigid.name] ||= {};
      // 动态体用 follow（退出解算）；挂件自带的碰撞板本就是运动学体,
      // follow 对它没意义，要用 no_collision 把掩码归零。
      state.profile.rigids[rigid.name].mode = rigid.mode === 0 ? 'no_collision' : 'follow';
    } else if (state.profile.rigids[rigid.name]) {
      // 只删 mode，别的调参（size_scale 等）保留
      delete state.profile.rigids[rigid.name].mode;
      if (!Object.keys(state.profile.rigids[rigid.name]).length) delete state.profile.rigids[rigid.name];
    }
  }
  status(follow ? `${groupLabel(group)} 已退出解算，点击“试烘当前动作”看效果`
                : `${groupLabel(group)} 已回到解算，点击“试烘当前动作”看效果`);
}

// 逐部件的显示开关。两个 Set 存「被隐藏的组名」——
// 用隐藏集而不是显示集，这样新载入的模型默认全显示，不用逐组初始化。
const hiddenMeshes = new Set(), hiddenColliders = new Set();

// 网格与碰撞体是两套无关的命名：submesh 用材质名（BodySkin/Cloth1A/Hair/Face…）,
// 碰撞体组用骨名（Skirt/HairI/BeltA…）。实测 jia 的匹配率是 0/21 —— 所以不做映射,
// 网格按自己的 submesh 名独立成列。hiddenMeshes 存的就是 submesh 名。

function selectedRigids() {
  return state.model ? state.model.rigids.filter(rigid => rigid.group === state.selected) : [];
}

function applySetting(key, value) {
  if (state.readOnly || state.loading || state.busy || !state.model) return;
  for (const rigid of selectedRigids()) {
    state.profile.rigids[rigid.name] ||= {};
    state.profile.rigids[rigid.name][key] = value;
  }
  status('参数已调整，点击“试烘当前动作”查看结果');
  if (state.run) element('right-label').textContent = '上次试烘（参数已修改）';
  updatePose();
}

for (const setting of partSettings) slider(element('sliders'), setting, applySetting);
for (const setting of solverSettings) slider(element('solver-controls'), setting, (key, value) => { state.profile.solver[key] = value; status('解算参数已调整，等待试烘'); });
for (let group = 0; group < 16; group++) {
  const label = document.createElement('label');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox'; checkbox.value = group;
  checkbox.setAttribute('aria-label', `排除碰撞组 ${group}`);
  checkbox.addEventListener('change', () => applySetting('blocked_groups', [...element('collision-groups').querySelectorAll('input:checked')].map(input => Number(input.value))));
  label.append(checkbox, document.createTextNode(String(group)));
  label.dataset.group = group;
  element('collision-groups').append(label);
}

// 碰撞组号是逐 PMX 作者定的 —— jia 的裙子在组 2、腿墙组 7、BeltA 组 5、BeltB 组 6,
// 而 eld 是裙子组 1、腿墙组 0。光看 0~15 的裸数字必然点错，且每错一次要等一次试烘。
// 所以每次载入模型都按实际刚体重新标注。
function labelCollisionGroups() {
  const rigids = state.model ? state.model.rigids : [];
  for (const label of element('collision-groups').querySelectorAll('label')) {
    const group = Number(label.dataset.group);
    const members = rigids.filter(rigid => rigid.collision_group === group);
    const checkbox = label.querySelector('input');
    // 空组勾了也改不到任何刚体，禁用掉省一次试烘
    checkbox.disabled = !members.length;
    label.classList.toggle('empty', !members.length);
    if (!members.length) {
      label.lastChild.textContent = ` ${group} —`;
      label.title = `组 ${group}：这个角色没有刚体`;
      continue;
    }
    // 按部件名归并取前两名当短标签。墙（mode 0）单独标注 ——
    // 免碰墙丢的是支撑，免碰挂件丢的只是挂件间互推，两者代价完全不同。
    const counts = new Map();
    for (const rigid of members) counts.set(rigid.group, (counts.get(rigid.group) || 0) + 1);
    const ranked = [...counts].sort((first, second) => second[1] - first[1]);
    const walls = members.filter(rigid => rigid.mode === 0).length;
    const brief = ranked.slice(0, 2).map(([name]) => name).join('/');
    label.lastChild.textContent = ` ${group} ${brief}${walls === members.length ? '(墙)' : ''}`;
    label.title = `组 ${group}：${members.length} 个刚体`
      + (walls ? `，其中 ${walls} 个是身体碰撞墙（免碰会丢支撑）` : '')
      + '\n' + ranked.map(([name, count]) => `${name} ×${count}`).join('、');
  }
}

function chooseGroup(group) {
  state.selected = group;
  const rigids = selectedRigids();
  element('part-controls').disabled = state.busy || state.loading || state.readOnly || !state.actors.length || !rigids.length;
  element('selection').textContent = groupLabel(group || '尚未选择');
  if (!rigids.length) return;
  const first = rigids[0];
  const overrides = state.profile.rigids[first.name] || {};
  const current = rigids.every(rigid => rigid.follow) ? '当前跟随依附骨' : rigids.every(rigid => rigid.mode === 0) ? '当前为身体碰撞墙' : '当前保留物理';
  element('selection-info').textContent = `${group} · ${rigids.length} 个碰撞体 · ${current}`;
  element('mode').value = overrides.mode || 'current';
  for (const [key, , , , , defaultValue] of partSettings) {
    const value = overrides[key] ?? defaultValue ?? first[key];
    for (const input of element('sliders').querySelectorAll(`[data-key="${key}"]`)) input.value = Number(value.toFixed(3));
  }
  for (const input of element('collision-groups').querySelectorAll('input')) input.checked = (overrides.blocked_groups || []).includes(Number(input.value));
  for (const button of element('groups').querySelectorAll('button')) button.classList.toggle('active', button.dataset.group === group);
  const selectedBones = new Set(rigids.map(rigid => rigid.bone));
  for (const actor of state.actors) for (const mesh of actor.meshes) {
    if (!mesh.isSkinnedMesh) continue;
    const colors = mesh.geometry.getAttribute('color');
    const indices = mesh.geometry.getAttribute('skinIndex');
    const weights = mesh.geometry.getAttribute('skinWeight');
    for (let vertex = 0; vertex < colors.count; vertex++) {
      let strength = 0;
      for (let influence = 0; influence < 4; influence++) {
        if (selectedBones.has(mesh.skeleton.bones[indices.getComponent(vertex, influence)]?.name)) strength += weights.getComponent(vertex, influence);
      }
      colors.setXYZ(vertex, 1, 1 - strength * 0.18, 1 - strength * 0.55);
    }
    colors.needsUpdate = true;
  }
  updatePose();
}

function populateGroups() {
  element('groups').replaceChildren();
  const groups = [...new Set(state.model.rigids.map(rigid => rigid.group))];
  const query = element('filter').value.toLowerCase();
  groups.sort((first, second) => Number(!/Skirt|Sleeve|Cape|Ribbon|Tie/.test(first)) - Number(!/Skirt|Sleeve|Cape|Ribbon|Tie/.test(second)) || first.localeCompare(second));
  for (const group of groups) {
    if (!(group + groupLabel(group)).toLowerCase().includes(query)) continue;
    const button = document.createElement('button');
    button.dataset.group = group;
    const title = document.createElement('span');
    title.textContent = groupLabel(group);
    const count = document.createElement('small');
    count.textContent = state.model.rigids.filter(rigid => rigid.group === group).length;
    button.append(title, count);
    // 「网」= 纯显示开关；「碰」= **物理开关**（写 profile 的 mode:'follow'）。
    // 两者语义不同：藏网格不改解算，而取消「碰」等于让这个部件退出解算。
    // stopPropagation 是必需的：勾选框在 button 内部，不拦住冒泡会同时触发选中。
    // 「物」= 物理开关；「碰」= 碰撞体是否显示。身体碰撞墙的物理锁死 ——
    // 它不参与解算，而摘掉它裙子会直接穿过身体（实测 max 24.43 → 115.04）。
    const wall = !hasDynamic(group) && !isFollowed(group);
    for (const [label, physical] of [['物', true], ['碰', false]]) {
      const toggle = document.createElement('input');
      toggle.type = 'checkbox';
      toggle.checked = physical ? !isFollowed(group) : !isNonColliding(group);
      // 「物」对身体墙锁死（它本就不参与解算，follow 对它没意义）；
      // 「碰」对任何组都可用 —— 那才是关掉一面墙的正确方式。
      toggle.disabled = state.busy || state.loading || state.readOnly || !state.actors.length || (physical && wall);
      toggle.title = physical
        ? (wall ? `${groupLabel(group)}是运动学碰撞墙，本就不参与解算；要关掉它请用右边的「碰」`
                : '参与物理解算（取消＝退出解算，等同不存在，需试烘生效）')
        : `挡住其他部件（取消＝这一组不再挡任何东西，仍参与解算，需试烘生效）`;
      toggle.setAttribute('aria-label', toggle.title);
      toggle.addEventListener('click', event => event.stopPropagation());
      toggle.addEventListener('change', () => {
        if (physical) setPartFollow(group, !toggle.checked);
        else setPartCollide(group, toggle.checked);
        populateGroups();
        updatePose();
      });
      const wrap = document.createElement('label');
      wrap.className = 'part-toggle';
      wrap.append(toggle, document.createTextNode(label));
      wrap.addEventListener('click', event => event.stopPropagation());
      button.append(wrap);
    }
    button.addEventListener('click', () => chooseGroup(group));
    element('groups').append(button);
  }
  element('part-count').textContent = `${groups.length} 组`;
  // 组号含义随角色变，跟着模型一起刷新
  labelCollisionGroups();
  populateMeshList();
}

// 网格列表按 submesh 名逐项勾选。与部件（碰撞体）列表分开是因为两套命名无关：
// jia 的 submesh 是 BodySkin/Cloth1A/Hair/Face…，碰撞体组是 Skirt/HairI/BeltA…
function populateMeshList() {
  const container = element('mesh-list');
  if (!container) return;
  container.replaceChildren();
  const seen = new Set();
  for (const actor of state.actors) {
    for (const mesh of actor.meshes) {
      const material = mesh.userData.material;
      if (!material || seen.has(material)) continue;
      seen.add(material);
      const row = document.createElement('label');
      row.className = 'mesh-row';
      const toggle = document.createElement('input');
      toggle.type = 'checkbox';
      toggle.checked = !hiddenMeshes.has(material);
      toggle.setAttribute('aria-label', `显示网格 ${material}`);
      toggle.addEventListener('change', () => {
        if (toggle.checked) hiddenMeshes.delete(material); else hiddenMeshes.add(material);
        updatePose();
      });
      row.append(toggle, document.createTextNode(material));
      container.append(row);
    }
  }
  element('mesh-count').textContent = seen.size ? `${seen.size} 件` : '';
}

function setControls() {
  const locked = state.busy || state.loading;
  for (const name of ['character', 'action', 'history', 'left-source']) element(name).disabled = state.busy;
  if (reviewMode) element('left-source').disabled = true;
  // A pending character/source catalog must finish before its action can change.
  element('action').disabled = locked;
  for (const name of ['bake', 'save', 'solver-controls', 'collider-all', 'collider-none', 'mesh-all', 'mesh-none']) {
    element(name).disabled = locked || state.readOnly || !state.actors.length;
  }
  element('part-controls').disabled = locked || state.readOnly || !state.actors.length || !selectedRigids().length;
  element('cancel').hidden = !state.busy;
}

function syncPreviewUrl(left, right, run) {
  const url = new URL(location.href);
  url.searchParams.set('character', element('character').value);
  url.searchParams.set('action', element('action').value);
  if (right !== 'base' && right !== 'trial') url.searchParams.set('source', right); else url.searchParams.delete('source');
  if (run) url.searchParams.set('run', run); else url.searchParams.delete('run');
  if (left !== 'current') url.searchParams.set('left_source', left); else url.searchParams.delete('left_source');
  window.history.replaceState(null, '', url);
}

// Each side resolves its own mesh and animation. A failed candidate request clears
// the stage, so an older actor can never masquerade as the newly selected source.
async function loadPreview({ reloadCatalog = false, trial = null, explicitAction = null } = {}) {
  const request = ++state.request;
  const character = element('character').value;
  const left = element('left-source').value;
  const right = state.evidencePreference || (trial ? 'trial' : 'base');
  state.loading = true; state.playing = false; state.run = trial; state.evidenceSource = state.evidencePreference;
  state.readOnly = serverReadOnly || left !== 'current' || !['base', 'current', 'trial'].includes(right);
  element('play').textContent = '播放';
  element('left-label').textContent = `${sourceLabels[left] || left} · 加载中`;
  element('right-label').textContent = `${sourceLabels[right] || right} · 加载中`;
  disposeActors();
  state.model = null;
  element('groups').replaceChildren();
  element('mesh-list').replaceChildren();
  element('model-warnings').textContent = '';
  if (reviewMode) {
    element('review-status').textContent = '正在加载样本…';
    element('review-focus').textContent = '';
    element('review-known').textContent = '';
    element('review-actions').replaceChildren();
  }
  if (state.evidencePreference) {
    const value = `evidence:${state.evidencePreference}`;
    if (![...element('history').options].some(option => option.value === value)) {
      element('history').add(new Option(sourceLabels[right] || right, value));
    }
    element('history').value = value;
  }
  setControls();
  syncPreviewUrl(left, right, trial);
  status('正在读取所选来源的模型与配对动作…');
  try {
    if (reloadCatalog) {
      const chosen = explicitAction || element('action').value;
      const catalogSource = [right, left].find(source => previewSources.has(source)) || 'current';
      const actionQuery = { character, source: catalogSource };
      // Keep a deep-link action visible even when it is outside the 50-action
      // focus list used for the fast first load.
      if (chosen) actionQuery.action = chosen;
      const actions = await api(`/api/actions?${new URLSearchParams(actionQuery)}`);
      if (request !== state.request) return;
      element('action').replaceChildren();
      for (const action of actions) {
        const note = !action.available ? ' · 缺少配对头部' : '';
        const option = new Option((action.label ? `${action.label} · ${action.id}` : action.id) + note, action.id);
        option.disabled = !action.available;
        option.title = action.unavailable_reason || '';
        element('action').add(option);
      }
      const preferred = actions.find(action => action.id === chosen && action.available) ||
        actions.find(action => action.id === 'idle' && action.available) || actions.find(action => action.available);
      if (explicitAction && !actions.some(action => action.id === explicitAction && action.available)) {
        throw new Error(`动作 ${explicitAction} 在所选来源中没有通过头身配对校验`);
      }
      if (!preferred) throw new Error('此角色在所选来源中没有可预览的配对动作');
      element('action').value = preferred.id;
    }
    const action = element('action').value;
    if (!action) throw new Error('请先选择可用动作');
    syncPreviewUrl(left, right, trial);
    const query = new URLSearchParams({ character, action });
    const getSide = async source => {
      const params = new URLSearchParams({ character, action, source });
      if (source === 'trial') params.set('run', trial);
      const [model, animation] = await Promise.all([api(`/api/model?${params}`), api(`/api/animation?${params}`)]);
      return { model, animation: decodeAnimation(animation) };
    };
    const profileRequest = trial ? api(`/api/trial-profile?${query}&run=${encodeURIComponent(trial)}`) :
      (reloadCatalog || state.readOnly || !state.profile || state.profile.character !== character) ?
        api(`/api/profile?character=${encodeURIComponent(character)}`) : Promise.resolve(state.profile);
    // Single-source review servers never fetch the other side: both slots show the same data.
    const sideRight = getSide(right);
    const sideLeft = onlySource ? sideRight : getSide(left);
    const [first, second, profile] = await Promise.all([sideLeft, sideRight, profileRequest]);
    if (request !== state.request) return;
    state.model = second.model; state.profile = profile;
    for (const side of [first, second]) {
      const actor = buildActor(side.model);
      actor.animation = side.animation;
      state.actors.push(actor);
    }
    state.frameCount = second.animation.body.frames; state.fps = second.animation.body.fps; state.frame = 0;
    element('frame').max = state.frameCount - 1;
    element('jump-frame').max = state.frameCount - 1;
    element('model-warnings').textContent = [...new Set([...first.model.warnings, ...second.model.warnings])].join('；');
    element('left-label').textContent = sourceLabels[left] || first.model.source_label || left;
    element('right-label').textContent = sourceLabels[right] || second.model.source_label || right;
    updateViewLabels();
    if (reviewMode) {
      const review = second.model.review || {};
      element('review-status').textContent = review.status || '对照预览';
      element('review-focus').textContent = review.focus || '';
      element('review-known').textContent = review.known || '';
      element('review-actions').replaceChildren();
      const titles = { idle: '待机', writing: '书写', sitting: '坐姿', dance_loop: '舞蹈', dance_spin: '转圈', emotion_happy: '开心', emotion_laugh: '大笑', emotion_shock: '惊讶', emotion_fear: '恐惧' };
      for (const id of review.actions || []) {
        const button = document.createElement('button');
        button.type = 'button'; button.textContent = titles[id] || id;
        button.classList.toggle('active', id === action);
        button.addEventListener('click', () => {
          element('action').value = id;
          loadAction().catch(handleError);
        });
        element('review-actions').append(button);
      }
    }
    for (const input of element('solver-controls').querySelectorAll('input')) input.value = profile.solver[input.dataset.key];
    state.loading = false;
    populateGroups(); chooseGroup(state.selected || 'Skirt');
    setControls(); updatePose();
    await refreshHistory(request);
    if (request !== state.request) return;
    status(`${state.frameCount} 帧 · ${state.fps.toFixed(2)} FPS · ${state.readOnly ? '只读对照，左右均使用各自来源的模型与动作' : '可播放、拖动时间轴或选择部件'}`);
  } catch (error) {
    if (request !== state.request) return;
    disposeActors();
    state.model = null;
    state.loading = false;
    element('left-label').textContent = `${sourceLabels[left] || left} · 预览不可用`;
    element('right-label').textContent = `${sourceLabels[right] || right} · 预览不可用`;
    if (reviewMode) {
      element('review-status').textContent = '样本未能加载';
      element('review-focus').textContent = '';
      element('review-known').textContent = '';
      element('review-actions').replaceChildren();
    }
    setControls();
    throw error;
  }
}

async function loadAction({ preserveEvidence = true } = {}) {
  if (!preserveEvidence) state.evidencePreference = null;
  return loadPreview({ reloadCatalog: !preserveEvidence });
}

async function loadCharacter(options = {}) {
  const request = state.request + 1;
  await loadPreview({ reloadCatalog: true, ...options });
  if (request === state.request && state.actors.length) resetCamera();
}

function handleError(error) {
  console.error(error);
  status(error.message || String(error), true);
}

function setBusy(busy) {
  state.busy = busy;
  setControls();
  if (state.model) populateGroups();
}

async function refreshHistory(request = state.request) {
  const character = element('character').value, action = element('action').value;
  if (onlySource) {
    element('history').replaceChildren(new Option(sourceLabels[onlySource] || onlySource, `evidence:${onlySource}`));
    element('history').disabled = true;
    return;
  }
  const history = await api(`/api/history?${new URLSearchParams({ character, action })}`);
  if (request !== state.request || character !== element('character').value || action !== element('action').value) return;
  element('history').replaceChildren(new Option('基础动作', ''), new Option('当前正式物理', 'evidence:current'));
  if (reviewMode) element('history').replaceChildren();
  const sources = new Set(['current']);
  for (const entry of history) {
    if (reviewMode && entry.source !== SAMPLE_SOURCE) continue;
    const date = new Date(entry.finished * 1000).toLocaleString();
    const label = entry.label ? `${entry.label} · ` : '';
    const value = entry.source ? `evidence:${entry.source}` : `trial:${entry.run}`;
    const duration = Number.isFinite(entry.seconds) ? ` · ${entry.seconds.toFixed(0)}秒` : '';
    element('history').add(new Option(`${label}${date}${duration}`, value));
    if (entry.source) { sources.add(entry.source); sourceLabels[entry.source] = entry.label || entry.source; }
  }
  // Keep the selected source visible even if this character has no candidate;
  // selecting it must produce an explicit error, never a silent fallback.
  for (const source of [reviewMode ? SAMPLE_SOURCE : EXPRESSION_SOURCE, state.evidencePreference]) {
    if (source && !sources.has(source)) {
      element('history').add(new Option(sourceLabels[source] || source, `evidence:${source}`));
      sources.add(source);
    }
  }
  element('history').value = state.evidencePreference ? `evidence:${state.evidencePreference}` : state.run ? `trial:${state.run}` : '';
}

async function loadTrial(run) {
  state.evidencePreference = null;
  element('compare').checked = true;
  return loadPreview({ trial: run, reloadCatalog: true });
}

async function loadEvidence(source) {
  state.evidencePreference = source;
  element('compare').checked = true;
  return loadPreview({ reloadCatalog: true });
}

async function trialBake() {
  setBusy(true);
  try {
    const result = await api('/api/bake', { profile: state.profile, action: element('action').value });
    state.job = result.run;
    element('job-details').open = true;
    const poll = async () => {
      try {
        const job = await api(`/api/job?run=${result.run}`);
        element('log').textContent = job.log;
        element('log').scrollTop = element('log').scrollHeight;
        element('job-title').textContent = `试烘 · ${job.seconds.toFixed(0)} 秒`;
        if (job.status === 'running') {
          status(`正在试烘 ${job.action} · ${job.seconds.toFixed(0)} 秒`);
          setTimeout(poll, 1500);
          return;
        }
        if (job.status === 'cancelled') {
          status('试烘已取消，正式物理文件未修改');
          state.job = null; setBusy(false);
          return;
        }
        if (job.status !== 'complete') throw new Error(job.error || '试烘失败，请查看记录；当前物理文件保持原样');
        await refreshHistory();
        await loadTrial(result.run);
        element('job-title').textContent = `试烘完成 · ${job.seconds.toFixed(0)} 秒`;
        element('log').textContent += `\n结果目录：${job.folder}`;
        updatePose();
        status('试烘完成：左侧当前物理，右侧本次结果，时间轴同步');
      } catch (error) { handleError(error); }
      setBusy(false);
      state.job = null;
    };
    await poll();
  } catch (error) {
    handleError(error);
    setBusy(false);
  }
}

let pointerStart = null;
renderer.domElement.addEventListener('pointerdown', event => { pointerStart = [event.clientX, event.clientY]; });
renderer.domElement.addEventListener('pointerup', event => {
  if (event.button !== 0 || !pointerStart || Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) > 5) return;
  const rectangle = renderer.domElement.getBoundingClientRect();
  pointer.set((event.clientX - rectangle.left) / rectangle.width * 2 - 1, -(event.clientY - rectangle.top) / rectangle.height * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const visibleActors = state.actors.filter(actor => actor.root.visible);
  for (const actor of visibleActors) for (const mesh of actor.meshes) {
    if (mesh.isSkinnedMesh) mesh.computeBoundingSphere();
  }
  const hits = raycaster.intersectObjects(visibleActors.flatMap(actor => actor.meshes), false);
  if (!hits.length) return;
  const hit = hits[0], mesh = hit.object;
  if (mesh.isSkinnedMesh && hit.face) {
    const indices = mesh.geometry.getAttribute('skinIndex'), weights = mesh.geometry.getAttribute('skinWeight');
    const scores = new Map();
    for (const vertex of [hit.face.a, hit.face.b, hit.face.c]) {
      for (let influence = 0; influence < 4; influence++) {
        const bone = mesh.skeleton.bones[indices.getComponent(vertex, influence)];
        const rigid = state.model.rigids.find(item => item.bone === bone?.name);
        if (rigid) scores.set(rigid.group, (scores.get(rigid.group) || 0) + weights.getComponent(vertex, influence));
      }
    }
    const selected = [...scores.entries()].sort((first, second) => second[1] - first[1])[0];
    if (selected) { chooseGroup(selected[0]); return; }
  }
  let closest = null, distance = Infinity;
  for (const actor of visibleActors) for (const collider of actor.colliders) {
    const candidate = new THREE.Vector3().setFromMatrixPosition(collider.matrix).distanceTo(hit.point);
    if (candidate < distance) { closest = collider.userData.rigid.group; distance = candidate; }
  }
  if (closest) chooseGroup(closest);
});

element('character').addEventListener('change', () => loadCharacter().catch(handleError));
element('left-source').addEventListener('change', () => loadCharacter().catch(handleError));
element('action').addEventListener('change', () => loadAction().catch(handleError));
element('history').addEventListener('change', () => {
  const value = element('history').value;
  if (!value) loadAction({ preserveEvidence: false }).catch(handleError);
  else {
    const [kind, id] = value.split(':', 2);
    (kind === 'trial' ? loadTrial(id) : loadEvidence(id)).catch(handleError);
  }
});
element('filter').addEventListener('input', () => { if (state.model) populateGroups(); });
element('mode').addEventListener('change', event => applySetting('mode', event.target.value));
element('reset-part').addEventListener('click', () => {
  for (const rigid of selectedRigids()) delete state.profile.rigids[rigid.name];
  chooseGroup(state.selected); status('此部件的参数已恢复，试烘后查看结果');
});
element('save').addEventListener('click', async () => {
  try { const result = await api('/api/profile', { profile: state.profile }); status(`方案已保存：${result.path}`); }
  catch (error) { handleError(error); }
});
element('bake').addEventListener('click', trialBake);
element('cancel').addEventListener('click', async () => {
  if (state.job) await api('/api/cancel', { run: state.job }).catch(handleError);
});
element('reset-camera').addEventListener('click', resetCamera);
// 批量勾选：只作用于**当前筛选可见**的组。筛选框里输了 Skirt 时点「全不选」,
// 不该把筛选外的组也隐藏 —— 那样清空筛选后会看到一堆莫名消失的部件。
for (const [id, physical, off] of [['mesh-all', false, false], ['mesh-none', false, true],
                                  ['collider-all', true, false], ['collider-none', true, true]]) {
  element(id).addEventListener('click', () => {
    for (const button of element('groups').querySelectorAll('button')) {
      const group = button.dataset.group;
      // 墙的物理锁死，与逐组勾选框保持一致；「碰」对任何组都可用
      if (physical && !hasDynamic(group) && !isFollowed(group)) continue;
      if (physical) setPartFollow(group, off);
      else setPartCollide(group, !off);
    }
    status(physical
      ? (off ? '所有可选部件已退出解算 —— 逐个勾回想保留的，再试烘'
             : '所有部件已回到解算，点击“试烘当前动作”看效果')
      : (off ? '所有部件已不挡东西 —— 逐个勾回想保留的，再试烘'
             : '所有部件已恢复碰撞，点击“试烘当前动作”看效果'));
    populateGroups();
    updatePose();
  });
}

function updateViewLabels() {
  const compared = element('compare').checked;
  element('left-label').hidden = reviewMode && !compared;
  element('right-label').hidden = !reviewMode && !compared;
  viewport.classList.toggle('single-candidate', reviewMode && !compared);
}

element('jump-frame').addEventListener('change', () => {
  const frame = Number(element('jump-frame').value);
  if (!Number.isFinite(frame)) return;
  state.playing = false; element('play').textContent = '播放';
  state.frame = Math.max(0, Math.min(state.frameCount - 1, Math.floor(frame)));
  element('jump-frame').value = state.frame;
  updatePose();
});

for (const name of ['colliders', 'wireframe', 'compare']) element(name).addEventListener('change', () => {
  updateViewLabels(); updatePose();
});
function togglePlayback() { state.playing = !state.playing; element('play').textContent = state.playing ? '暂停' : '播放'; }
element('play').addEventListener('click', togglePlayback);
element('frame').addEventListener('input', event => {
  state.playing = false; element('play').textContent = '播放';
  state.frame = Number(event.target.value); updatePose();
});
for (const [name, step] of [['previous', -1], ['next', 1]]) element(name).addEventListener('click', () => {
  state.playing = false; element('play').textContent = '播放';
  state.frame = Math.max(0, Math.min(state.frameCount - 1, Math.floor(state.frame) + step)); updatePose();
});
document.addEventListener('keydown', event => {
  if (event.code === 'Space' && !['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(document.activeElement.tagName)) {
    event.preventDefault(); togglePlayback();
  }
});
let previousTime = performance.now();
renderer.setAnimationLoop(now => {
  const elapsed = Math.min(0.1, (now - previousTime) / 1000); previousTime = now;
  if (state.playing) { state.frame = (state.frame + elapsed * state.fps * Number(element('speed').value)) % state.frameCount; updatePose(); }
  controls.update();
  renderer.render(scene, camera);
});

try {
  const catalog = await api('/api/catalog');
  serverReadOnly = catalog.read_only;
  onlySource = catalog.only_source || null;
  const sampleCharacters = catalog.sources.find(source => source.id === SAMPLE_SOURCE)?.characters || [];
  for (const character of catalog.characters) {
    if (!reviewMode || sampleCharacters.includes(character.id)) element('character').add(new Option(character.name, character.id));
  }
  if (reviewMode) {
    document.body.classList.add('sample-review');
    document.title = 'Moe · 五角色样本检查';
    element('review-panel').hidden = false;
    element('save').hidden = true;
    element('left-source').replaceChildren(new Option('当前正式物理', 'current'));
    element('left-source').disabled = true;
  }
  for (const action of catalog.focus) element('action').add(new Option(action, action));
  // 证据链接可直接打开指定角色、动作和只读来源，普通入口仍保持默认选择。
  const initial = new URLSearchParams(location.search);
  const character = initial.get('character') || (reviewMode ? 'jia' : null), action = initial.get('action'), source = initial.get('source') || (reviewMode ? SAMPLE_SOURCE : null);
  if ([...element('character').options].some(option => option.value === character)) element('character').value = character;
  if (action && ![...element('action').options].some(option => option.value === action)) element('action').add(new Option(action, action));
  if (action) element('action').value = action;
  for (const sourceInfo of catalog.sources || []) {
    if (!sourceLabels[sourceInfo.id]) sourceLabels[sourceInfo.id] = sourceInfo.label;
    if (!reviewMode && ![...element('left-source').options].some(option => option.value === sourceInfo.id)) element('left-source').add(new Option(sourceLabels[sourceInfo.id], sourceInfo.id));
  }
  const left = initial.get('left_source') || 'current';
  if (![...element('left-source').options].some(option => option.value === left)) throw new Error(`未知左侧来源：${left}`);
  element('left-source').value = left;
  if (source && source !== 'base') state.evidencePreference = source;
  await loadCharacter({ explicitAction: action, trial: initial.get('run') });
  if (initial.has('frame')) {
    const frame = Number(initial.get('frame'));
    if (Number.isFinite(frame)) { state.frame = Math.max(0, Math.min(state.frameCount - 1, Math.floor(frame))); updatePose(); }
  }
} catch (error) { handleError(error); }
