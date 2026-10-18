// moe_bullet_bake —— 原生 Bullet 的 MMD 物理烘焙器，一比一照 saba 的 MMDPhysics.cpp。
//
// 为什么不用 Blender 内置 Bullet（2026-09-05 与用户定案，见 todo.md 第 3 节）：
//   · Blender 的碰撞过滤是对称单字段 AND，表达不出 MMD 的 group/mask 两位域，
//     只能逐对建 NonCollision 约束——艾尔黛拉 107284 对，装配 1330 s，且进求解器循环
//   · 重力 / 休眠 / 关节类型 / 步长都是 Blender 侧的替代品，与 MMD 原配置不同
// 这里 group/mask 直接喂 `addRigidBody(body, group, mask)`，屏蔽对象数为 0。
//
// **坐标系**：全程 PMX 原生空间（左手、Y 上、单位≈8 cm）。不做 saba 的 InvZ——
// 我们 .anim 空间 = PMX × scale，轴序不变无镜像（洛茜 head 实测 (0,114.057,0.989) 两边
// 完全相等），所以 Python 侧只做一次缩放。手性在刚体解算里不重要（Bullet 不在乎左右手，
// 只要输入输出用同一套）。
//
// **欧拉角顺序**：PMX 刚体/关节的 rotation 是 DirectX YawPitchRoll = Ry(y)·Rx(x)·Rz(z)。
// 判据：胶囊局部 Y 轴应顺着骨向，洛茜 88 个多轴旋转的胶囊在 YXZ 下中位 0.00°，
// 其他五种顺序 0.9~2°（work/evidence 见 todo）。saba 关节用 setEulerZYX 与其刚体不一致，
// 这里刚体与关节都用 YXZ。
//
// 输入：
//   scene.txt   文本，每行一条记录（见 loadScene）
//   frames.bin  int32 F, int32 NB, 然后 F×NB×7 float32：每帧每根 PMX 骨的世界 (pos xyz, quat xyzw)
// 输出：
//   out.bin     int32 F, int32 NR, 然后 F×NR×7 float32：每帧每个刚体的世界 (pos, quat)
//
// 每帧流程（照 saba UpdatePhysicsAnimation）：运动学刚体（模式 0）随骨走 →
// stepSimulation(1/fps, 10, 1/120) → 读全部刚体世界变换。
// 循环动画整段跑 warmup 圈再记最后一圈；非循环在第 0 帧静置 settle 步再记一圈。
#include "btBulletDynamicsCommon.h"
#include "BulletDynamics/ConstraintSolver/btGeneric6DofSpring2Constraint.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <fstream>
#include <memory>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Bone { int parent; btVector3 pos; };
struct Rigid {
    int bone, shape, mode, group, mask;
    btVector3 size, pos, rot;
    float mass, linDamp, angDamp, restitution, friction;
};
struct Joint {
    int a, b;
    int soft = 1;
    btVector3 pos, rot, linLo, linHi, angLo, angHi, sprLin, sprAng;
};
struct Scene {
    float gravity = -98.0f;   // saba: -9.8 × 10（PMX 尺度）
    float fps = 30.0f;
    std::vector<Bone> bones;
    std::vector<Rigid> rigids;
    std::vector<Joint> joints;
};

void die(const std::string& m) { std::fprintf(stderr, "[moe_bullet] **%s**\n", m.c_str()); std::exit(2); }

btVector3 rd3(std::istringstream& stream) {
    float first = 0, second = 0, third = 0;
    if (!(stream >> first >> second >> third) || !std::isfinite(first) || !std::isfinite(second) || !std::isfinite(third)) {
        die("invalid vector in scene record: " + stream.str());
    }
    return btVector3(first, second, third);
}

Scene loadScene(const std::string& path) {
    std::ifstream f(path);
    if (!f) die("打不开 scene: " + path);
    Scene sc; std::string line;
    std::map<std::string, int> expected;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream s(line); std::string k; s >> k;
        if (k == "gravity") s >> sc.gravity;
        else if (k == "fps") s >> sc.fps;
        else if (k == "bone") { Bone b{}; s >> b.parent; b.pos = rd3(s); sc.bones.push_back(b); }
        else if (k == "rigid") {
            Rigid r{}; s >> r.bone >> r.shape; r.size = rd3(s); r.pos = rd3(s); r.rot = rd3(s);
            s >> r.mass >> r.linDamp >> r.angDamp >> r.restitution >> r.friction >> r.mode >> r.group >> r.mask;
            for (float value : {r.mass, r.linDamp, r.angDamp, r.restitution, r.friction}) {
                if (!std::isfinite(value) || value < 0) die("invalid rigid parameter: " + line);
            }
            if (r.shape < 0 || r.shape > 2 || r.mode < 0 || r.mode > 2 || r.group < 0 || r.group > 15 || r.mask < 0 || r.mask > 65535) {
                die("invalid rigid mode/shape/collision group: " + line);
            }
            if (r.size.x() <= 0 || r.size.y() < 0 || r.size.z() < 0 || (r.shape == 1 && (r.size.y() <= 0 || r.size.z() <= 0))) {
                die("invalid rigid size: " + line);
            }
            sc.rigids.push_back(r);
        } else if (k == "joint") {
            Joint j{}; s >> j.a >> j.b; j.pos = rd3(s); j.rot = rd3(s);
            j.linLo = rd3(s); j.linHi = rd3(s); j.angLo = rd3(s); j.angHi = rd3(s); j.sprLin = rd3(s); j.sprAng = rd3(s);
            if (!s.eof()) {
                s >> std::ws;
                if (!s.eof()) s >> j.soft;
            }
            if (j.soft != 0 && j.soft != 1) die("invalid joint soft flag: " + line);
            sc.joints.push_back(j);
        } else if (k == "nbones" || k == "nrigids" || k == "njoints") {
            int count = -1;
            s >> count;
            if (count < 0 || expected.count(k)) die("invalid scene count: " + line);
            expected[k] = count;
        }
        else die("scene 里不认识的记录: " + k);
        if (s.fail()) die("incomplete or invalid scene record: " + line);
        std::string extra;
        if (s >> extra) die("unexpected data in scene record: " + line);
    }
    if (!std::isfinite(sc.fps) || sc.fps <= 0 || !std::isfinite(sc.gravity)) die("invalid scene fps/gravity");
    for (const auto& count : expected) {
        size_t actual = count.first == "nbones" ? sc.bones.size() : count.first == "nrigids" ? sc.rigids.size() : sc.joints.size();
        if (size_t(count.second) != actual) die("scene count mismatch: " + count.first);
    }
    for (const auto& bone : sc.bones) {
        if (bone.parent < -1 || bone.parent >= int(sc.bones.size())) die("invalid bone parent index");
    }
    for (const auto& rigid : sc.rigids) {
        if (rigid.bone < -1 || rigid.bone >= int(sc.bones.size())) die("invalid rigid bone index");
    }
    for (const auto& joint : sc.joints) {
        if (joint.a < -1 || joint.b < -1 || joint.a >= int(sc.rigids.size()) || joint.b >= int(sc.rigids.size())) die("invalid joint rigid index");
    }
    return sc;
}

// PMX 欧拉 (x,y,z) → 旋转，YXZ 顺序（DirectX YawPitchRoll）。
btQuaternion eulerYXZ(const btVector3& e) {
    btQuaternion qx(btVector3(1, 0, 0), e.x()), qy(btVector3(0, 1, 0), e.y()), qz(btVector3(0, 0, 1), e.z());
    return qy * qx * qz;
}

struct Frames {
    int F = 0, NB = 0;
    std::vector<float> data;   // F×NB×7
    btTransform boneWorld(int f, int b) const {
        const float* p = &data[(size_t(f) * NB + b) * 7];
        return btTransform(btQuaternion(p[3], p[4], p[5], p[6]), btVector3(p[0], p[1], p[2]));
    }
};

Frames loadFrames(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) die("打不开 frames: " + path);
    Frames fr;
    f.read(reinterpret_cast<char*>(&fr.F), 4); f.read(reinterpret_cast<char*>(&fr.NB), 4);
    if (fr.F <= 0 || fr.NB <= 0) die("frames 头无效");
    fr.data.resize(size_t(fr.F) * fr.NB * 7);
    f.read(reinterpret_cast<char*>(fr.data.data()), fr.data.size() * 4);
    if (!f) die("frames 数据不足");
    if (!std::all_of(fr.data.begin(), fr.data.end(), [](float value) { return std::isfinite(value); })) die("non-finite animation input");
    return fr;
}

// 运动学刚体（模式 0）：Bullet 每次 stepSimulation 开头调 getWorldTransform 取位姿并算速度
// （btRigidBody::saveKinematicState），与 saba 的 KinematicMotionState 同构。
struct KinematicMS : btMotionState {
    btTransform cur;
    void getWorldTransform(btTransform& t) const override { t = cur; }
    void setWorldTransform(const btTransform&) override {}
};

struct Body {
    std::unique_ptr<btCollisionShape> shape;
    std::unique_ptr<btMotionState> ms;
    std::unique_ptr<btRigidBody> rb;
    btTransform offset;      // 刚体相对所属骨（rest 下）：rbWorld = boneWorld · offset
    btTransform restWorld;   // PMX rest 下的世界位姿 = 模型初始状态（--intro 的起点）
    int bone = -1, mode = 1;
};

}  // namespace

int main(int argc, char** argv) {
    if (argc < 4) { std::fprintf(stderr, "用法: moe_bullet_bake scene.txt frames.bin out.bin [--warmup N] [--intro N] [--loop] [--settle N] [--gravity g] [--split-impulse-threshold T] [--linear-slop S] [--contact-stiffness k] [--contact-damping c]\n"); return 1; }
    std::string scenePath = argv[1], framesPath = argv[2], outPath = argv[3];
    std::string contactReport;
    int warmup = 3, settle = 60; bool loop = false; bool gravOverride = false; float gravOv = 0;
    // 入场过渡步数：墙从 PMX rest 插值到第 0 帧，动态刚体全靠物理跟。
    // seatAll(0) 是把裙格硬塞到第 0 帧位置（坐姿时腿已抬起，裙格可能已在墙内），
    // 预热重复的是同一个错误起点，所以圈数救不回来。
    int intro = 60;
    // 过渡结束后在第 0 帧姿态原地静置的步数。**默认 0：实测有害。**
    // 绛雨坐姿矩阵（入场 60 固定）：预热1+静置0 kick 31.2/峰 29.71，预热1+静置120 39.2/40.41，
    // 预热3+静置0 28.2/20.44，预热3+静置120 36.7/47.00 —— 加静置两档都变差。
    // 原因：裙子在坐姿姿态下**不存在静止平衡点**（关掉腿墙后 96 条通道全被摘成常量 =
    // 全部运动都来自腿墙挤压），静置只是让它在接触面上多振一会儿，攒下的速度带进记录段。
    // 开关留着供别的角色 A/B。
    int introHold = 0;
    // 接触刚度/阻尼（Bullet issue #4428 的 setContactStiffnessAndDamping）。
    // 静止接触抖动是 Bullet 有编号的已知问题（issue #3049 Resting contacts problem）：
    // 顺序冲量解算器 + 穿透恢复项在接触面上「推出 vs 拉回」打架。ERP 只改恢复强度，
    // 接触刚度直接把接触当弹簧-阻尼器，是比 ERP 更贴近问题的那个量。
    // 负数 = 不设（用 Bullet 默认的刚性接触）。
    float contactStiff = -1, contactDamp = -1;
    // 入场期把运动学墙缩小到这个比例，过渡结束线性恢复到 1.0。
    // 抄 Houdini Vellum Drape 的 Inflate Collisions：初始布料已与碰撞体相交时，
    // 先让碰撞体瘪下去，再在若干帧内膨胀回原尺寸，就能得到无穿透的垂落结果
    // （我们的坐姿正是「初始已相交」那一类）。1.0 = 不缩。
    float introDeflate = 1.0f;
    // 循环动画在最后一圈之后续录的帧数（重新驱动帧 0..K-1，物理不重置）。Python 侧用它做接缝交叉淡入：
    // 本圈第 i 帧与续录第 i 帧是同一套运动学下的两段真实轨迹，混合后接缝两侧速度连续。
    int loopExtra = 0;
    // ---- 风场（阶段二，用户 2026-09-05：躯干不动也要轻微来回摆，头发目标 10°）
    // 风 = 主风向 × S × (base + (1-base)·sin(2π·k1·t/T + φ1)) + 侧向 × S·turb × sin(2π·k2·t/T + φ2)
    // k1/k2 是**整数**周期数（循环动画的接缝才不回来），t 以帧为单位、T = 帧数。
    // 模式 force：F 直接是力（重挂件如 10 万质量的尾巴天然不受风，轻头发受风最多，接近气动阻力
    // 「按面积不按质量」的直觉）；模式 accel：F = m·a，等价于倾斜重力，所有挂件同加速度。
    float windS = 0.0f; btVector3 windDir(1, 0, 0.4f); float windPeriod = 2.5f; float windTurb = 0.35f;
    float windBase = 0.5f; bool windAccel = false;
    int iters = 10;   // Bullet 默认 = saba 用的；A/B 变量：全锁关节是软约束，高加速度下被拉开时加迭代能收紧
    bool spring2 = false;              // 隐式弹簧关节（自动限刚度），A/B 变量
    float erp = -1.0f, erp2 = -1.0f;   // <0 = Bullet 默认（0.2 / 0.2）
    // 接触穿透的两个容差量，<0 = 不动 Bullet 默认。
    // splitImpulseTh 传正数，内部写成负值（Bullet 那个字段是负的，默认 -0.04）：
    // 穿透浅于它的接触，位置修正项会被当成真实速度冲量注入 = 推出时带多余动能。
    // 我们是 PMX 尺度（重力 98、裙格半径 0.45），0.04 等于比 Bullet 标定的米制紧一个量级。
    float splitImpulseTh = -1.0f, linearSlop = -1.0f;
    // 限位轴 ERP（2026-09-07 抖动定因）：Spring2 的限位停止行默认拿世界 erp。骨顶在角限位上时，一帧的越界量
    // 被一个子步以 0.2 硬弹回 = 单帧 10~57° 的踢击后衰减（探针量得抖动是 3~6 Hz 随躯干节律的踢击，不是 15 Hz 颤）。
    // 全局降 --erp 虽消抖，但锁死轴（lo==hi，MMD 里绝大多数是 6 轴锁的球关节）也一起软化，裙子分离率 74%→150%。
    // 所以只对**有活动范围**的轴单独设 STOP_ERP，锁死轴保持世界值。<0 = 不设。
    float erpLimit = -1.0f;
    // 活动范围小于该值（弧度）的轴按锁死对待、不软化：洛茜裙子关节是 ±2.5°（不是 6 轴锁死），软化后跪拜时被腿墙
    // 推着漂移，prostration_loop 分离率 11.6%→227%。默认 6° 全范围（裙 5° 排除，头发/刘海 20° 纳入）。
    float erpLimitMinRange = 6.0f * 3.14159265f / 180.0f;
    float hz = 120.0f; // 固定子步频率，saba 120；A/B 变量：迭代 60 后 dance_spin 尾根仍 47% 峰，剩下的是步长
    for (int i = 4; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--warmup" && i + 1 < argc) warmup = std::atoi(argv[++i]);
        else if (a == "--intro" && i + 1 < argc) intro = std::atoi(argv[++i]);
        else if (a == "--intro-hold" && i + 1 < argc) introHold = std::atoi(argv[++i]);
        else if (a == "--contact-stiffness" && i + 1 < argc) contactStiff = float(std::atof(argv[++i]));
        else if (a == "--contact-damping" && i + 1 < argc) contactDamp = float(std::atof(argv[++i]));
        else if (a == "--intro-deflate" && i + 1 < argc) introDeflate = float(std::atof(argv[++i]));
        else if (a == "--settle" && i + 1 < argc) settle = std::atoi(argv[++i]);
        else if (a == "--loop") loop = true;
        else if (a == "--loop-extra" && i + 1 < argc) loopExtra = std::atoi(argv[++i]);
        else if (a == "--gravity" && i + 1 < argc) { gravOverride = true; gravOv = float(std::atof(argv[++i])); }
        else if (a == "--wind" && i + 1 < argc) windS = float(std::atof(argv[++i]));
        else if (a == "--wind-dir" && i + 3 < argc) { float x = float(std::atof(argv[++i])), y = float(std::atof(argv[++i])), z = float(std::atof(argv[++i])); windDir = btVector3(x, y, z); }
        else if (a == "--wind-period" && i + 1 < argc) windPeriod = float(std::atof(argv[++i]));   // 秒
        else if (a == "--wind-turb" && i + 1 < argc) windTurb = float(std::atof(argv[++i]));
        else if (a == "--wind-base" && i + 1 < argc) windBase = float(std::atof(argv[++i]));
        else if (a == "--wind-accel") windAccel = true;
        else if (a == "--iters" && i + 1 < argc) iters = std::atoi(argv[++i]);
        else if (a == "--hz" && i + 1 < argc) hz = float(std::atof(argv[++i]));
        else if (a == "--erp" && i + 1 < argc) erp = float(std::atof(argv[++i]));
        else if (a == "--erp-limit" && i + 1 < argc) erpLimit = float(std::atof(argv[++i]));
        else if (a == "--erp-limit-min-range" && i + 1 < argc) erpLimitMinRange = float(std::atof(argv[++i])) * 3.14159265f / 180.0f;   // 度
        else if (a == "--spring2") spring2 = true;
        else if (a == "--erp2" && i + 1 < argc) erp2 = float(std::atof(argv[++i]));
        else if (a == "--split-impulse-threshold" && i + 1 < argc) splitImpulseTh = float(std::atof(argv[++i]));
        else if (a == "--linear-slop" && i + 1 < argc) linearSlop = float(std::atof(argv[++i]));
        else if (a == "--contact-report" && i + 1 < argc) contactReport = argv[++i];
        else die("不认识的参数: " + a);
    }
    Scene sc = loadScene(scenePath);
    Frames fr = loadFrames(framesPath);
    if (fr.NB != int(sc.bones.size())) die("scene/frame bone count mismatch");
    if (fr.NB != int(sc.bones.size())) die("frames 骨数 " + std::to_string(fr.NB) + " != scene 骨数 " + std::to_string(sc.bones.size()));
    if (gravOverride) sc.gravity = gravOv;

    // ---- 世界（照 saba MMDPhysics::Create）
    btDefaultCollisionConfiguration cfg;
    btCollisionDispatcher dispatcher(&cfg);
    btDbvtBroadphase broadphase;
    btSequentialImpulseConstraintSolver solver;
    btDiscreteDynamicsWorld world(&dispatcher, &broadphase, &solver, &cfg);
    world.setGravity(btVector3(0, sc.gravity, 0));
    world.getSolverInfo().m_numIterations = iters;
    if (erp >= 0) world.getSolverInfo().m_erp = erp;      // 关节
    if (erp2 >= 0) world.getSolverInfo().m_erp2 = erp2;   // 接触（推出穿透的硬度）
    // 往 0 挪 = 让浅穿透也走 split impulse 的 push 通道，不把位置修正注入速度。
    // 实测（jia，manifold 真实穿透量）：坐姿 32 对「浅且久」接触、dance_spin 0 对，
    // 与抖动单调对应 —— kick%/穿透深度/频谱都给不出这个区分。
    if (splitImpulseTh >= 0) world.getSolverInfo().m_splitImpulsePenetrationThreshold = -splitImpulseTh;
    // Bullet 默认 0.0 = 要求把穿透精确压到零，求解器一直在争最后那一点点。
    if (linearSlop >= 0) world.getSolverInfo().m_linearSlop = linearSlop;
    // saba 还加了一块 y=0 地面；我们的角色脚在 y≈0，裙摆不会低于地面，但坐姿/跪姿会——
    // 原版 CK3 肖像没有地面碰撞的概念，**不加地面**，否则跪姿的裙子会被地面顶起。

    // ---- 刚体（照 MMDRigidBody::Create(PMXRigidbody)）
    std::vector<Body> bodies(sc.rigids.size());
    int nKin = 0, nDyn = 0;
    for (size_t i = 0; i < sc.rigids.size(); ++i) {
        const Rigid& r = sc.rigids[i]; Body& b = bodies[i];
        switch (r.shape) {
            case 0: b.shape.reset(new btSphereShape(r.size.x())); break;
            case 1: b.shape.reset(new btBoxShape(r.size)); break;
            case 2: b.shape.reset(new btCapsuleShape(r.size.x(), r.size.y())); break;
            default: die("刚体 " + std::to_string(i) + " 形状未知 " + std::to_string(r.shape));
        }
        btScalar mass = (r.mode == 0) ? 0 : r.mass;
        btVector3 inertia(0, 0, 0);
        if (mass != 0) b.shape->calculateLocalInertia(mass, inertia);
        btTransform rbWorld(eulerYXZ(r.rot), r.pos);
        // PMX 骨 rest 只有平移没有旋转，所以 inv(boneRest) 就是平移 -pos。
        btTransform boneRest(btQuaternion::getIdentity(), (r.bone >= 0 ? sc.bones[r.bone].pos : btVector3(0, 0, 0)));
        b.offset = boneRest.inverse() * rbWorld;
        b.restWorld = rbWorld;   // 模型初始状态，--intro 从这里插值到第 0 帧
        b.bone = r.bone; b.mode = r.mode;
        if (r.mode == 0) { b.ms.reset(new KinematicMS()); static_cast<KinematicMS*>(b.ms.get())->cur = rbWorld; ++nKin; }
        else { b.ms.reset(new btDefaultMotionState(rbWorld)); ++nDyn; }
        btRigidBody::btRigidBodyConstructionInfo info(mass, b.ms.get(), b.shape.get(), inertia);
        info.m_linearDamping = r.linDamp; info.m_angularDamping = r.angDamp;
        info.m_restitution = r.restitution; info.m_friction = r.friction;
        info.m_additionalDamping = true;
        b.rb.reset(new btRigidBody(info));
        // 接触刚度只给动态刚体：墙是运动学体（质量 0），接触的另一侧才决定刚度。
        if (r.mode != 0 && contactStiff > 0)
            b.rb->setContactStiffnessAndDamping(contactStiff, contactDamp > 0 ? contactDamp : 0);
        b.rb->setUserIndex(int(i));
        b.rb->setSleepingThresholds(0.01f, btRadians(0.1f));
        b.rb->setActivationState(DISABLE_DEACTIVATION);
        if (r.mode == 0) b.rb->setCollisionFlags(b.rb->getCollisionFlags() | btCollisionObject::CF_KINEMATIC_OBJECT);
        // MMD 的 group 是 0..15 的序号，mask 是 16 位「与哪些组碰撞」（PMX 原文位 1 = 碰撞）。
        world.addRigidBody(b.rb.get(), int(1 << r.group), int(r.mask));
    }

    // ---- 关节（照 MMDJoint::CreateJoint(PMXJoint)）
    std::vector<std::unique_ptr<btTypedConstraint>> cons;
    for (const Joint& j : sc.joints) {
        if (j.a < 0 || j.b < 0 || j.a == j.b || j.a >= int(bodies.size()) || j.b >= int(bodies.size())) continue;
        btTransform jw(eulerYXZ(j.rot), j.pos);
        btTransform inA = bodies[j.a].rb->getWorldTransform().inverse() * jw;
        btTransform inB = bodies[j.b].rb->getWorldTransform().inverse() * jw;
        if (spring2) {
            // 隐式弹簧：setStiffness(limitIfNeeded=true) 按 m/dt² 限刚度，1e5 的錘弹簧不再自激
            auto* c = new btGeneric6DofSpring2Constraint(*bodies[j.a].rb, *bodies[j.b].rb, inA, inB, RO_XYZ);
            c->setLinearLowerLimit(j.linLo); c->setLinearUpperLimit(j.linHi);
            c->setAngularLowerLimit(j.angLo); c->setAngularUpperLimit(j.angHi);
            for (int k = 0; k < 3; ++k) {
                if (j.sprLin[k] != 0) { c->enableSpring(k, true); c->setStiffness(k, j.sprLin[k], true); c->setDamping(k, 1.0f, true); }
                if (j.sprAng[k] != 0) { c->enableSpring(k + 3, true); c->setStiffness(k + 3, j.sprAng[k], true); c->setDamping(k + 3, 1.0f, true); }
            }
            // 按组排除（2026-09-07 第三轮）：洛茜裙子 144 个 20° 角轴跪拜时被腿墙持续推着，软化后稳态越界累积、整链折过去
            // （prostration_loop 分离率 11.6%→227%，与芙洛伦头发甩翻是同一种链式代价）。裙子基线抖动本就低，不软化。
            if (erpLimit >= 0 && j.soft) {
                // 只给有活动范围的轴（线 0..2 / 角 3..5）设限位 ERP；锁死轴不碰，仍走世界 erp
                for (int k = 0; k < 3; ++k) {
                    // 线性轴**不**软化（2026-09-07 第二轮）：洛茜 158 个关节带线性活动范围（芙洛伦 0 个），线性限位一软
                    // 就是刚体被推开、骨拉长——分离率量的正是这个，prostration_loop 裙子 11.6%→227% 的真凶；角范围阈值救不回来。
                    if (j.angHi[k] - j.angLo[k] > erpLimitMinRange) c->setParam(BT_CONSTRAINT_STOP_ERP, erpLimit, k + 3);
                }
            }
            c->setEquilibriumPoint();
            world.addConstraint(c); cons.emplace_back(c);
        } else {
            auto* c = new btGeneric6DofSpringConstraint(*bodies[j.a].rb, *bodies[j.b].rb, inA, inB, true);
            c->setLinearLowerLimit(j.linLo); c->setLinearUpperLimit(j.linHi);
            c->setAngularLowerLimit(j.angLo); c->setAngularUpperLimit(j.angHi);
            for (int k = 0; k < 3; ++k) {
                if (j.sprLin[k] != 0) { c->enableSpring(k, true); c->setStiffness(k, j.sprLin[k]); }
                if (j.sprAng[k] != 0) { c->enableSpring(k + 3, true); c->setStiffness(k + 3, j.sprAng[k]); }
            }
            world.addConstraint(c); cons.emplace_back(c);
        }
    }

    // ---- 落座到第 0 帧（照 saba ResetPhysics：全部刚体先按骨摆好、清速度）
    // f < 0 = 用 PMX rest（模型初始状态）落座，供 --intro 起点用
    auto seatAll = [&](int f) {
        for (Body& b : bodies) {
            btTransform w = f < 0 ? b.restWorld
                : (b.bone >= 0 ? fr.boneWorld(f, b.bone) : btTransform::getIdentity()) * b.offset;
            if (b.mode == 0) static_cast<KinematicMS*>(b.ms.get())->cur = w;
            b.rb->setWorldTransform(w); b.rb->setInterpolationWorldTransform(w);
            b.rb->setLinearVelocity(btVector3(0, 0, 0)); b.rb->setAngularVelocity(btVector3(0, 0, 0)); b.rb->clearForces();
            b.rb->activate(true);
        }
        world.getBroadphase()->resetPool(&dispatcher);
    };
    auto driveKinematic = [&](int f) {
        for (Body& b : bodies)
            if (b.mode == 0 && b.bone >= 0) static_cast<KinematicMS*>(b.ms.get())->cur = fr.boneWorld(f, b.bone) * b.offset;
    };
    const btScalar dt = btScalar(1.0) / sc.fps;
    // 风的两个整数周期数：主风按 --wind-period 秒折成整数圈（至少 1），湍流取主风圈数×1.618 取整
    // 再至少 +1 保证与主风不同（同周期就只是换个相位，看不出「乱」）。非循环动画不必整数，但整数也无害。
    const float T = float(fr.F);
    int k1 = std::max(1, int(std::lround(T / sc.fps / std::max(windPeriod, 0.1f))));
    int k2 = std::max(k1 + 1, int(std::lround(k1 * 1.618f)));
    btVector3 wdir = windDir.length2() > 0 ? windDir.normalized() : btVector3(1, 0, 0);
    btVector3 wlat = wdir.cross(btVector3(0, 1, 0)); if (wlat.length2() < 1e-6f) wlat = btVector3(0, 0, 1); wlat.normalize();
    auto applyWind = [&](float t) {
        if (windS == 0) return;
        const float two_pi = 6.2831853f;
        float g1 = windBase + (1.0f - windBase) * std::sin(two_pi * k1 * t / T + 0.7f);
        float g2 = std::sin(two_pi * k2 * t / T + 2.1f);
        btVector3 w = wdir * (windS * g1) + wlat * (windS * windTurb * g2);
        for (Body& b : bodies) {
            if (b.mode == 0) continue;
            b.rb->applyCentralForce(windAccel ? w * b.rb->getMass() : w);
        }
    };
    // 力在 stepSimulation 内跨全部子步保留、结束时 clearForces（btDiscreteDynamicsWorld 的语义），
    // 所以每帧 step 前施加一次即可。
    // maxSubSteps 要 ≥ hz/fps，否则 Bullet 会把多出来的时间丢掉（动画变慢）；saba 是 10 @120 Hz。
    const int maxSub = std::max(10, int(std::ceil(hz / sc.fps)) + 1);
    auto step = [&](float t) { applyWind(t); world.stepSimulation(dt, maxSub, btScalar(1.0) / hz); };

    // ---- 入场过渡：从模型初始状态（rest）渐变到第 0 帧姿态
    // 只驱动运动学墙，动态刚体（裙/发）不去摆它 —— 它们被抬起的腿一点点推到正确一侧，
    // 这才是「裙子落到腿上」。之后 warmup/settle 再从这个正确拓扑出发。
    if (intro > 0) {
        seatAll(-1);
        for (int s = 0; s < intro; ++s) {
            const float u = float(s + 1) / float(intro);
            // 墙尺寸从 introDeflate 线性恢复到 1.0（球/胶囊改半径，盒子不动）
            if (introDeflate < 1.0f) {
                const float scale = introDeflate + (1.0f - introDeflate) * u;
                for (size_t bi = 0; bi < bodies.size(); ++bi) {
                    Body& b = bodies[bi];
                    if (b.mode != 0) continue;
                    const int shp = sc.rigids[bi].shape;
                    if (shp == 0) static_cast<btSphereShape*>(b.shape.get())
                        ->setUnscaledRadius(sc.rigids[bi].size.x() * scale);
                    else if (shp == 2) b.shape->setLocalScaling(btVector3(scale, 1, scale));
                }
            }
            for (Body& b : bodies) {
                if (b.mode != 0) continue;
                const btTransform& t0 = b.restWorld;
                const btTransform t1 = (b.bone >= 0 ? fr.boneWorld(0, b.bone) : btTransform::getIdentity()) * b.offset;
                btTransform mid(t0.getRotation().slerp(t1.getRotation(), btScalar(u)),
                                t0.getOrigin() * btScalar(1 - u) + t1.getOrigin() * btScalar(u));
                static_cast<KinematicMS*>(b.ms.get())->cur = mid;
            }
            // 风相位倒着数到帧 0，记录段开始时风已稳定（与 settle 段同理）
            step(float(s - intro - introHold));
        }
        // 墙尺寸复原（浮点插值到 u=1 未必精确等于原值，显式写回）
        if (introDeflate < 1.0f) {
            for (size_t bi = 0; bi < bodies.size(); ++bi) {
                Body& b = bodies[bi];
                if (b.mode != 0) continue;
                const int shp = sc.rigids[bi].shape;
                if (shp == 0) static_cast<btSphereShape*>(b.shape.get())
                    ->setUnscaledRadius(sc.rigids[bi].size.x());
                else if (shp == 2) b.shape->setLocalScaling(btVector3(1, 1, 1));
            }
        }
        // 过渡结束后在第 0 帧姿态原地静置：坐姿腿墙在动画内几乎不动（0.0019/帧），
        // 但入场要走 10.39 单位 —— 过渡期墙速必然远超动画内速度，裙子跟不上。
        // 这段让它在最终姿态下落定（动态刚体仍自由，墙已到位）。
        driveKinematic(0);
        for (int s = 0; s < introHold; ++s) step(float(s - introHold));
    } else {
        seatAll(0);
    }
    if (loop) {
        for (int c = 0; c < warmup; ++c)
            for (int f = 0; f < fr.F; ++f) { driveKinematic(f); step(float(f)); }
    } else {
        // 静置期风的相位倒着数到帧 0，记录段开始时风已在「当前值」上稳定，不会从无风突变到有风
        driveKinematic(0);
        for (int s = 0; s < settle; ++s) step(float(s - settle));
    }

    // ---- 记录最后一圈（循环动画再续录 loopExtra 帧：帧索引回到 0..K-1，物理状态接着走）
    const int nRec = fr.F + (loop ? std::max(0, loopExtra) : 0);
    struct ContactStats { int frames = 0; float depth = 0; };
    std::map<std::pair<int, int>, ContactStats> contacts;
    std::vector<float> out(size_t(nRec) * bodies.size() * 7);
    for (int f = 0; f < nRec; ++f) {
        const int kf = f % fr.F;
        driveKinematic(kf); step(float(f));
        if (!contactReport.empty()) {
            for (int manifoldIndex = 0; manifoldIndex < dispatcher.getNumManifolds(); ++manifoldIndex) {
                const auto* manifold = dispatcher.getManifoldByIndexInternal(manifoldIndex);
                float depth = 0;
                for (int pointIndex = 0; pointIndex < manifold->getNumContacts(); ++pointIndex)
                    depth = std::max(depth, float(-manifold->getContactPoint(pointIndex).getDistance()));
                if (depth <= 0) continue;
                int first = manifold->getBody0()->getUserIndex();
                int second = manifold->getBody1()->getUserIndex();
                if (first > second) std::swap(first, second);
                auto& entry = contacts[{first, second}];
                ++entry.frames;
                entry.depth = std::max(entry.depth, depth);
            }
        }
        for (size_t i = 0; i < bodies.size(); ++i) {
            const btTransform& t = bodies[i].rb->getCenterOfMassTransform();
            float* p = &out[(size_t(f) * bodies.size() + i) * 7];
            p[0] = t.getOrigin().x(); p[1] = t.getOrigin().y(); p[2] = t.getOrigin().z();
            btQuaternion q = t.getRotation();
            p[3] = q.x(); p[4] = q.y(); p[5] = q.z(); p[6] = q.w();
        }
    }
    std::ofstream o(outPath, std::ios::binary);
    if (!o) die("写不了 out: " + outPath);
    int F = nRec, NR = int(bodies.size());   // 头里写的是**记录帧数**（含续录段）
    o.write(reinterpret_cast<const char*>(&F), 4); o.write(reinterpret_cast<const char*>(&NR), 4);
    o.write(reinterpret_cast<const char*>(out.data()), out.size() * 4);
    if (!contactReport.empty()) {
        std::ofstream report(contactReport);
        if (!report) die("Cannot write contact report: " + contactReport);
        report << "rigid_a,rigid_b,frames,max_depth\n";
        for (const auto& entry : contacts)
            report << entry.first.first << ',' << entry.first.second << ','
                   << entry.second.frames << ',' << entry.second.depth << '\n';
    }
    std::printf("[moe_bullet] 刚体 %d（运动学 %d 动态 %d）关节 %zu 帧 %d(+%d) %s 重力 %.1f 风 %.2f%s(k1=%d k2=%d turb %.2f) 迭代 %d 子步 %.0fHz erp2 %.2f splitTh %.4f slop %.4f %s → %s\n",
                NR, nKin, nDyn, cons.size(), fr.F, nRec - fr.F, (("入场" + std::to_string(intro) + "+" + std::to_string(introHold) + " ") + (loop ? "循环×" + std::to_string(warmup) : "静置" + std::to_string(settle))).c_str(),
                sc.gravity, windS, windAccel ? "a" : "F", k1, k2, windTurb, iters, hz, double(world.getSolverInfo().m_erp2),
                double(world.getSolverInfo().m_splitImpulsePenetrationThreshold),
                double(world.getSolverInfo().m_linearSlop),
                spring2 ? "spring2" : "spring", outPath.c_str());
    for (auto& c : cons) world.removeConstraint(c.get());
    for (Body& b : bodies) world.removeRigidBody(b.rb.get());
    return 0;
}
