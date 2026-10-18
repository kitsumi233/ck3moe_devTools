// Experimental PMX rigid-body baker. Same scene/frames/output contract as moe_bullet.
// Native D6 uses swing/twist, NOT Bullet RO_XYZ. Explicit opt-in is required.
// All coordinates are scaled by 0.1 internally; returned poses stay in PMX units.
#include "PxPhysicsAPI.h"
#include "extensions/PxD6Joint.h"
#include "extensions/PxDefaultCpuDispatcher.h"
#include "cudamanager/PxCudaContextManager.h"
#include "gpu/PxGpu.h"
#include <Windows.h>
#include <DbgHelp.h>
#pragma comment(lib, "dbghelp.lib")
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

using namespace physx;
namespace fs = std::filesystem;
namespace {
constexpr float scale = 0.1f;
constexpr float pi = 3.14159265358979323846f;
using Clock = std::chrono::steady_clock;
double seconds(Clock::time_point a) {
    return std::chrono::duration<double>(Clock::now() - a).count();
}
void require(bool ok, const std::string &msg) {
    if (!ok)
        throw std::runtime_error(msg);
}
std::vector<int> rigidSelection(const std::string &list) {
    require(!list.empty() && list.back() != ',', "empty rigid selection");
    std::istringstream stream(list);
    std::string item;
    std::vector<int> indices;
    while (std::getline(stream, item, ',')) {
        size_t n = 0;
        const int index = std::stoi(item, &n);
        require(n == item.size() && index >= 0, "invalid rigid index");
        require(std::find(indices.begin(), indices.end(), index) == indices.end(), "duplicate rigid index");
        indices.push_back(index);
    }
    std::sort(indices.begin(), indices.end());
    return indices;
}
std::string indicesText(const std::vector<int> &indices) {
    std::string text;
    for (int index : indices) {
        if (!text.empty()) text += ',';
        text += std::to_string(index);
    }
    return text;
}
struct Bone {
    int parent = -1;
    PxVec3 pos{0};
};
struct Rigid {
    int bone = -1, shape = 0, mode = 0, group = 0, mask = 0;
    PxVec3 size{0}, pos{0}, rot{0};
    float mass = 0, linDamp = 0, angDamp = 0, restitution = 0, friction = 0;
};
struct Joint {
    int a = -1, b = -1, soft = 1;
    PxVec3 pos{0}, rot{0}, linLo{0}, linHi{0}, angLo{0}, angHi{0}, sprLin{0}, sprAng{0};
    // Optional candidate response contract: frequencies in Hz, damping ratios.
    // Legacy scenes do not contain a response record and retain authored drives.
    bool response = false;
    float angularHz = 0, angularRatio = 1, linearHz = 0, linearRatio = 1;
    // Opt-in, single-DOF anti-twist constraint around the authored joint Z.
    // Separate from Bullet Euler bounds and the primary joint's translation.
    float twistZLimit = -1; // absent=-1, hard zero-twist lock=0, limited=>0
};
struct Scene {
    float fps = 30, gravity = -98;
    std::vector<Bone> bones;
    std::vector<Rigid> rigids;
    std::vector<Joint> joints;
    struct BoneTwist { int child=-1, parent=-1, parentBone=-1; PxQuat frame{PxIdentity}; };
    std::vector<BoneTwist> boneTwists;
};
PxVec3 rd3(std::istringstream &s) {
    PxVec3 v(0);
    s >> v.x >> v.y >> v.z;
    require(!s.fail() && v.isFinite(), "invalid vector: " + s.str());
    return v;
}
Scene loadScene(const fs::path &path) {
    std::ifstream in(path);
    require(bool(in), "cannot read scene");
    Scene sc;
    std::string line;
    std::map<std::string, int> counts;
    while (std::getline(in, line)) {
        std::istringstream s(line);
        std::string key;
        s >> key;
        if (key.empty() || key[0] == '#')
            continue;
        if (key == "fps")
            s >> sc.fps;
        else if (key == "gravity")
            s >> sc.gravity;
        else if (key == "bone") {
            Bone b;
            s >> b.parent;
            b.pos = rd3(s);
            sc.bones.push_back(b);
        } else if (key == "rigid") {
            Rigid r;
            s >> r.bone >> r.shape;
            r.size = rd3(s);
            r.pos = rd3(s);
            r.rot = rd3(s);
            s >> r.mass >> r.linDamp >> r.angDamp >> r.restitution >> r.friction >> r.mode >> r.group >>
                r.mask;
            for (float v : {r.mass, r.linDamp, r.angDamp, r.restitution, r.friction})
                require(std::isfinite(v) && v >= 0, "invalid rigid scalar");
            require(r.shape >= 0 && r.shape <= 2 && r.mode >= 0 && r.mode <= 2 && r.group >= 0 &&
                        r.group < 16 && r.mask >= 0 && r.mask <= 65535,
                    "invalid rigid flags");
            require(r.size.x > 0 && r.size.y >= 0 && r.size.z >= 0 &&
                        (r.shape != 1 || (r.size.y > 0 && r.size.z > 0)),
                    "invalid shape size");
            require(r.mode == 0 || r.mass > 0, "dynamic mass must be positive");
            require(r.restitution <= 1, "restitution exceeds PhysX range");
            sc.rigids.push_back(r);
        } else if (key == "joint") {
            Joint j;
            s >> j.a >> j.b;
            j.pos = rd3(s);
            j.rot = rd3(s);
            j.linLo = rd3(s);
            j.linHi = rd3(s);
            j.angLo = rd3(s);
            j.angHi = rd3(s);
            j.sprLin = rd3(s);
            j.sprAng = rd3(s);
            s >> std::ws;
            if (!s.eof())
                s >> j.soft;
            require(j.soft == 0 || j.soft == 1, "invalid joint soft flag");
            for (int k = 0; k < 3; ++k)
                require(j.sprLin[k] >= 0 && j.sprAng[k] >= 0, "negative spring stiffness");
            sc.joints.push_back(j);
        } else if (key == "response") {
            int index = -1;
            float ah = 0, az = 0, lh = 0, lz = 0;
            s >> index >> ah >> az >> lh >> lz;
            require(index >= 0 && size_t(index) < sc.joints.size(), "response joint must already exist");
            auto &j = sc.joints[index];
            require(!j.response, "duplicate response joint");
            require(std::isfinite(ah) && std::isfinite(az) && std::isfinite(lh) && std::isfinite(lz) &&
                    ah >= 0 && ah <= 30 && lh >= 0 && lh <= 60 && az > 0 && az <= 5 && lz > 0 && lz <= 5,
                    "invalid response frequency/damping ratio");
            j.response = true;
            j.angularHz = ah; j.angularRatio = az; j.linearHz = lh; j.linearRatio = lz;
        } else if (key == "twist_z") {
            int index = -1;
            float limit = 0;
            s >> index >> limit;
            require(index >= 0 && size_t(index) < sc.joints.size(), "twist_z joint must already exist");
            auto &j = sc.joints[index];
            require(j.twistZLimit < 0, "duplicate twist_z joint");
            require(std::isfinite(limit) && limit >= 0 && limit < pi, "invalid twist_z range");
            require(j.a >= 0 && j.b >= 0 && j.a != j.b, "invalid twist_z endpoints");
            j.twistZLimit = limit;
        } else if (key == "bone_twist") {
            Scene::BoneTwist lock;
            s >> lock.child >> lock.parent >> lock.parentBone >> lock.frame.x >> lock.frame.y >> lock.frame.z >> lock.frame.w;
            require(lock.child>=0 && size_t(lock.child)<sc.rigids.size() && sc.rigids[lock.child].mode!=0,
                    "invalid bone_twist child");
            require(lock.parent>=-1 && lock.parent<int(sc.rigids.size()) && lock.parent!=lock.child,
                    "invalid bone_twist parent");
            require(lock.parentBone>=-1 && lock.parentBone<int(sc.bones.size()) && (lock.parent>=0 || lock.parentBone>=0),
                    "invalid bone_twist reference bone");
            require(lock.frame.isFinite() && std::abs(lock.frame.magnitudeSquared()-1)<1e-4f,"invalid bone_twist frame");
            for(const auto &old:sc.boneTwists) require(old.child!=lock.child,"duplicate bone_twist child");
            lock.frame.normalize();sc.boneTwists.push_back(lock);
        } else if (key == "nbones" || key == "nrigids" || key == "njoints") {
            int n = -1;
            s >> n;
            require(n >= 0 && !counts.count(key), "invalid scene count");
            counts[key] = n;
        } else
            throw std::runtime_error("unknown scene record: " + key);
        require(!s.fail(), "incomplete scene record: " + line);
        std::string extra;
        require(!(s >> extra), "extra scene data: " + line);
    }
    require(std::isfinite(sc.fps) && sc.fps > 0 && std::isfinite(sc.gravity), "invalid fps/gravity");
    for (const auto &p : counts)
        require(size_t(p.second) == (p.first == "nbones"    ? sc.bones.size()
                                     : p.first == "nrigids" ? sc.rigids.size()
                                                            : sc.joints.size()),
                "scene count mismatch");
    for (const auto &b : sc.bones)
        require(b.parent >= -1 && b.parent < int(sc.bones.size()), "bone parent out of range");
    for (const auto &r : sc.rigids)
        require(r.bone >= -1 && r.bone < int(sc.bones.size()), "rigid bone out of range");
    for (const auto &j : sc.joints)
        require(j.a >= -1 && j.b >= -1 && j.a < int(sc.rigids.size()) && j.b < int(sc.rigids.size()),
                "joint index out of range");
    return sc;
}
PxQuat eulerYXZ(const PxVec3 &e) {
    return PxQuat(e.y, PxVec3(0, 1, 0)) * PxQuat(e.x, PxVec3(1, 0, 0)) * PxQuat(e.z, PxVec3(0, 0, 1));
}
PxQuat eulerXYZ(const PxVec3 &e) {
    return PxQuat(e.x, PxVec3(1, 0, 0)) * PxQuat(e.y, PxVec3(0, 1, 0)) * PxQuat(e.z, PxVec3(0, 0, 1));
}
PxTransform lerp(const PxTransform &a, const PxTransform &b, float t) {
    return PxTransform(a.p + (b.p - a.p) * t, PxSlerp(t, a.q, b.q));
}
struct Frames {
    int F = 0, NB = 0;
    std::vector<float> data;
    PxTransform bone(int f, int b) const {
        if (b < 0)
            return PxTransform(PxIdentity);
        const float *p = &data[(size_t(f) * NB + b) * 7];
        return PxTransform(PxVec3(p[0], p[1], p[2]) * scale, PxQuat(p[3], p[4], p[5], p[6]).getNormalized());
    }
};
Frames loadFrames(const fs::path &path) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    require(bool(in), "cannot read frames");
    auto bytes = in.tellg();
    in.seekg(0);
    Frames f;
    in.read(reinterpret_cast<char *>(&f.F), 4);
    in.read(reinterpret_cast<char *>(&f.NB), 4);
    require(bool(in) && f.F > 0 && f.NB > 0, "invalid frame header");
    const uint64_t n = uint64_t(f.F) * f.NB * 7;
    require(n < 1000000000 && uint64_t(bytes) == 8 + n * 4, "invalid frame length");
    f.data.resize(size_t(n));
    in.read(reinterpret_cast<char *>(f.data.data()), std::streamsize(n * 4));
    for (size_t i = 0; i < f.data.size(); i += 7) {
        for (int k = 0; k < 7; ++k)
            require(std::isfinite(f.data[i + k]), "non-finite frame data");
        float q = 0;
        for (int k = 3; k < 7; ++k)
            q += f.data[i + k] * f.data[i + k];
        require(std::abs(q - 1) < 0.01f, "invalid input quaternion");
    }
    return f;
}
struct Options {
    bool gpu = false, loop = false, allowApprox = false;
    bool tgs = true;
    int warmup = 3, settle = 180, intro = 60, introHold = 0, extra = 0, iters = 8, velocityIters = 1,
        threads = 1;
    float hz = 120, gravity = 0, contactOffset = 0.04f, driveDamping = 1, maxDepenetration = 10;
    // <=0: same as maxDepenetration. Otherwise the cap switches to this value once an
    // instance starts recording, so intro/settle still separate interlocked layers fast.
    float recordDepenetration = 0;
    // Depth-graded depenetration: a body whose deepest contact is <= depthLo gets
    // depthVelLo, >= depthHi gets maxDepenetration, linear in between. Updated
    // after every substep from that substep's contacts (one substep of lag).
    bool depthDepen = false;
    float depthLo = 0, depthHi = 0, depthVelLo = 0;
    float angularLimitHz = 0;
    std::vector<int> angularLimitRigids;
    float contactFrequencyHz = 0, contactDampingRatio = 1;
    std::vector<int> contactRigids;
    std::string contactRigidIndices;
    float wind = 0, windPeriod = 5, windTurb = 0.35f, windBase = 0.5f;
    PxVec3 windDir{1, 0, 0.4f};
    bool windAccel = false;
    bool boneTwistProjection = true;
    bool overrideGravity = false, disablePreprocessing = false, externalForcesEveryIteration = false;
    fs::path batch, contacts;
    std::vector<fs::path> positional;
};
Options parse(const std::vector<std::string> &args) {
    Options o;
    for (size_t i = 1; i < args.size(); ++i) {
        auto a = args[i];
        auto value = [&]() {
            require(i + 1 < args.size(), "missing argument: " + a);
            return args[++i];
        };
        auto integer = [&]() {
            auto v = value();
            size_t n = 0;
            int x = std::stoi(v, &n);
            require(n == v.size(), "invalid integer");
            return x;
        };
        auto real = [&]() {
            auto v = value();
            size_t n = 0;
            float x = std::stof(v, &n);
            require(n == v.size() && std::isfinite(x), "invalid float");
            return x;
        };
        if (a == "--device") {
            auto v = value();
            require(v == "cpu" || v == "gpu", "unknown device");
            o.gpu = v == "gpu";
        } else if (a == "--solver") {
            auto v = value();
            require(v == "tgs" || v == "pgs", "unknown constraint solver");
            o.tgs = v == "tgs";
        } else if (a == "--allow-angular-approximation")
            o.allowApprox = true;
        else if (a == "--batch")
            o.batch = fs::u8path(value());
        else if (a == "--contact-report")
            o.contacts = fs::u8path(value());
        else if (a == "--warmup")
            o.warmup = integer();
        else if (a == "--settle")
            o.settle = integer();
        else if (a == "--intro")
            o.intro = integer();
        else if (a == "--intro-hold")
            o.introHold = integer();
        else if (a == "--loop")
            o.loop = true;
        else if (a == "--loop-extra")
            o.extra = integer();
        else if (a == "--iters")
            o.iters = integer();
        else if (a == "--velocity-iters")
            o.velocityIters = integer();
        else if (a == "--threads")
            o.threads = integer();
        else if (a == "--hz")
            o.hz = real();
        else if (a == "--gravity") {
            o.gravity = real();
            o.overrideGravity = true;
        } else if (a == "--contact-offset")
            o.contactOffset = real();
        else if (a == "--drive-damping")
            o.driveDamping = real();
        else if (a == "--max-depenetration-velocity")
            o.maxDepenetration = real();
        else if (a == "--record-depenetration-velocity")
            o.recordDepenetration = real();
        else if (a == "--depth-depenetration") {
            o.depthDepen = true;
            o.depthLo = real();
            o.depthHi = real();
            o.depthVelLo = real();
        }
        else if (a == "--angular-limit-hz")
            o.angularLimitHz = real();
        else if (a == "--angular-limit-rigids")
            o.angularLimitRigids = rigidSelection(value());
        else if (a == "--contact-frequency-hz")
            o.contactFrequencyHz = real();
        else if (a == "--contact-damping-ratio")
            o.contactDampingRatio = real();
        else if (a == "--contact-rigids") {
            o.contactRigids = rigidSelection(value());
            o.contactRigidIndices = indicesText(o.contactRigids);
        }
        else if (a == "--external-forces-every-iteration")
            o.externalForcesEveryIteration = true;
        else if (a == "--disable-preprocessing")
            o.disablePreprocessing = true;
        else if (a == "--disable-bone-twist-projection")
            o.boneTwistProjection = false;
        else if (a == "--wind")
            o.wind = real();
        else if (a == "--wind-period")
            o.windPeriod = real();
        else if (a == "--wind-turb")
            o.windTurb = real();
        else if (a == "--wind-base")
            o.windBase = real();
        else if (a == "--wind-dir") {
            const float x = real(), y = real(), z = real();
            o.windDir = PxVec3(x, y, z);
        } else if (a == "--wind-accel")
            o.windAccel = true;
        else if (a.rfind("--", 0) == 0)
            throw std::runtime_error("unsupported option: " + a);
        else
            o.positional.push_back(fs::u8path(a));
    }
    require(o.allowApprox, "D6 swing/twist differs from Bullet RO_XYZ; pass --allow-angular-approximation "
                           "for isolated prototype tests");
    require(o.iters > 0 && o.iters <= 255 && o.velocityIters >= 0 && o.velocityIters <= 255 &&
                o.threads > 0 && o.threads <= 64,
            "invalid solver iterations/threads");
    require(std::isfinite(o.recordDepenetration) && o.recordDepenetration >= 0, "invalid record depenetration velocity");
    require(!o.depthDepen || (std::isfinite(o.depthLo) && std::isfinite(o.depthHi) && std::isfinite(o.depthVelLo) &&
                              o.depthLo >= 0 && o.depthHi > o.depthLo && o.depthVelLo > 0),
            "invalid depth depenetration (lo depth >= 0, hi depth > lo, lo velocity > 0)");
    require(!(o.depthDepen && o.recordDepenetration > 0), "depth and record depenetration cannot be combined");
    require(o.hz > 0 && o.hz <= 10000 && o.contactOffset > 0 && o.driveDamping >= 0 && o.maxDepenetration > 0,
            "invalid physical options");
    require(o.angularLimitHz >= 0 && o.angularLimitHz <= 1000, "invalid angular limit frequency");
    require(o.angularLimitRigids.empty() || o.angularLimitHz > 0,
            "angular limit rigid selection requires nonzero angular limit frequency");
    require(o.contactFrequencyHz >= 0 && o.contactFrequencyHz <= 1000 &&
                o.contactDampingRatio >= 0 && o.contactDampingRatio <= 10,
            "invalid compliant contact frequency/damping");
    require(o.contactRigids.empty() || o.contactFrequencyHz > 0,
            "compliant rigid selection requires nonzero contact frequency");
    require(o.tgs || !o.externalForcesEveryIteration,
            "external forces every iteration requires TGS");
    require(o.wind >= 0 && o.windPeriod >= 0.1f && o.windTurb >= 0 &&
                o.windBase >= 0 && o.windBase <= 1 && o.windDir.magnitudeSquared() > 1e-12f &&
                std::isfinite(o.windDir.magnitudeSquared()), "invalid wind options");
    require(o.warmup >= 0 && o.settle >= 0 && o.intro >= 0 && o.introHold >= 0 && o.extra >= 0,
            "negative frame count");
    require((o.batch.empty() && o.positional.size() == 3) || (!o.batch.empty() && o.positional.empty()),
            "expected scene frames output, or --batch manifest.tsv");
    return o;
}
struct Errors : PxErrorCallback {
    std::atomic<int> count{0};
    void reportError(PxErrorCode::Enum code, const char *message, const char *file, int line) override {
        std::cerr << "[PhysX " << int(code) << "] " << message << " (" << file << ":" << line << ")\n";
        if (code != PxErrorCode::eDEBUG_INFO)
            ++count;
    }
    void check() {
        require(count == 0, "PhysX reported an error/warning; output rejected");
    }
};
PxFilterFlags filter(PxFilterObjectAttributes, PxFilterData a, PxFilterObjectAttributes, PxFilterData b,
                     PxPairFlags &flags, const void *, PxU32) {
    if (a.word2 != b.word2 || !(a.word0 & b.word1) || !(b.word0 & a.word1))
        return PxFilterFlag::eSUPPRESS;
    flags = PxPairFlag::eCONTACT_DEFAULT;
    if (a.word3 || b.word3)
        flags |= PxPairFlag::eNOTIFY_TOUCH_FOUND | PxPairFlag::eNOTIFY_TOUCH_PERSISTS |
                 PxPairFlag::eNOTIFY_CONTACT_POINTS;
    return PxFilterFlag::eDEFAULT;
}
struct Body {
    PxRigidDynamic *actor = nullptr;
    PxTransform rest{PxIdentity}, offset{PxIdentity}, previous{PxIdentity};
    int mode = 0, bone = -1;
};
struct JointFrames {
    size_t index;
    PxTransform a, b;
};
#include "twist_z.h"
struct Instance {
    Scene sc;
    Frames frames;
    fs::path out;
    bool loop = false;
    PxVec3 origin{0};
    std::vector<Body> bodies;
    std::vector<JointFrames> jointFrames;
    struct ResponseDrive {
        PxD6Joint *joint;
        int a, b;
        PxTransform frameA, frameB;
        PxQuat target;
        float velocityGain;
        PxVec3 active;
        bool slerp;
    };
    std::vector<ResponseDrive> responseDrives;
    struct TwistZLimit {
        TwistZConstraint *joint;
        size_t sourceIndex;
        float limit;
    };
    std::vector<TwistZLimit> twistZLimits;
    struct BoneLock {
        TwistZConstraint *joint;
        int child, parent, parentBone;
        PxRigidDynamic *reference;
        PxTransform frameA, frameB;
        PxTransform previous{PxIdentity}, target{PxIdentity};
    };
    std::vector<BoneLock> boneLocks;
    double maxBoneTwistBefore=0,maxBoneTwistAfter=0,maxBoneTwistCorrection=0,maxBoneTwistCorrectionRecorded=0,maxBoneSwingDirectionDelta=0;
    size_t boneTwistCorrectionsOver1=0,boneTwistCorrectionsOver10=0,boneTwistCorrectionsOver45=0;
    int maxBoneTwistCorrectionChild=-1,maxBoneTwistCorrectionTick=-1,maxBoneTwistCorrectionRecordedChild=-1,maxBoneTwistCorrectionRecordedFrame=-1;
    std::vector<float> output;
    std::vector<PxTransform> targets;
    int startRecord = 0, total = 0, recorded = 0, skippedStatic = 0, skippedInvalid = 0, shiftedLocks = 0;
    int compliantAngularJoints = 0;
    int responseJoints = 0, responseSlerpJoints = 0, responseAxisJoints = 0;
    bool removed = false;
    double maxAngleError = 0, maxLinearError = 0;
    double maxTwistZAngle = 0, maxTwistZError = 0;
    double maxTwistZLockError = 0;
    int twistZLockedJoints = 0;
    int maxLinearJoint = -1, maxLinearFrame = -1, maxLinearAxis = -1;
    int windCycles = 1, windSideCycles = 2;
    float windGustGain = 1;
    std::map<std::pair<int, int>, std::pair<int, float>> contacts;
    std::map<std::pair<int, int>, float> frameContacts;
    std::vector<float> bodyDepth;  // deepest penetration this substep, PMX units
    double depthDepenSum = 0;
    size_t depthDepenSamples = 0, depthDepenAtMax = 0;
};
struct ContactCallback : PxSimulationEventCallback {
    std::vector<Instance> *instances = nullptr;
    bool invalidInstance = false;
    void onContact(const PxContactPairHeader &header, const PxContactPair *pairs, PxU32 count) override {
        if (header.flags &
            (PxContactPairHeaderFlag::eREMOVED_ACTOR_0 | PxContactPairHeaderFlag::eREMOVED_ACTOR_1))
            return;
        for (PxU32 i = 0; i < count; ++i) {
            const auto &p = pairs[i];
            if (p.flags & (PxContactPairFlag::eREMOVED_SHAPE_0 | PxContactPairFlag::eREMOVED_SHAPE_1))
                continue;
            auto d = p.shapes[0]->getSimulationFilterData();
            if (d.word2 >= instances->size() ||
                p.shapes[1]->getSimulationFilterData().word2 != d.word2) {
                invalidInstance = true;
                continue;
            }
            auto &inst = (*instances)[d.word2];
            int a = int(reinterpret_cast<uintptr_t>(header.actors[0]->userData)) - 1,
                b = int(reinterpret_cast<uintptr_t>(header.actors[1]->userData)) - 1;
            if (a > b)
                std::swap(a, b);
            std::vector<PxContactPairPoint> points(p.contactCount);
            auto n = p.extractContacts(points.data(), p.contactCount);
            for (PxU32 k = 0; k < n; ++k)
                if (points[k].separation < 0) {
                    const float depth = -points[k].separation / scale;
                    inst.frameContacts[{a, b}] = std::max(inst.frameContacts[{a, b}], depth);
                    for (int body : {a, b})
                        if (body >= 0 && size_t(body) < inst.bodyDepth.size())
                            inst.bodyDepth[body] = std::max(inst.bodyDepth[body], depth);
                }
        }
    }
    void onConstraintBreak(PxConstraintInfo *, PxU32) override {}
    void onWake(PxActor **, PxU32) override {}
    void onSleep(PxActor **, PxU32) override {}
    void onTrigger(PxTriggerPair *, PxU32) override {}
    void onAdvance(const PxRigidBody *const *, const PxTransform *, const PxU32) override {}
};
using MaterialKey = std::tuple<float, float, bool>;
using MaterialCache = std::map<MaterialKey, PxMaterial *>;

PxMaterial &materialFor(PxPhysics &physics, MaterialCache &materials, const Rigid &r, const Options &o, bool compliant) {
    const MaterialKey key{r.friction, r.restitution, compliant};
    auto found = materials.find(key);
    if (found != materials.end())
        return *found->second;
    auto *material = physics.createMaterial(r.friction, r.friction, r.restitution);
    require(material != nullptr, "material creation failed");
    material->setFrictionCombineMode(PxCombineMode::eMULTIPLY);
    material->setRestitutionCombineMode(PxCombineMode::eMULTIPLY);
    if (compliant) {
        // A physical compliant contact, solved inside PhysX at every substep.
        // Acceleration springs give a common frequency across authored masses.
        // Average equal parameters: multiplying two negative stiffness values
        // would square the requested stiffness and destroy the frequency contract.
        const float omega = 2 * pi * o.contactFrequencyHz;
        material->setRestitution(-omega * omega);
        material->setDamping(2 * o.contactDampingRatio * omega);
        material->setFlag(PxMaterialFlag::eCOMPLIANT_ACCELERATION_SPRING, true);
        material->setRestitutionCombineMode(PxCombineMode::eAVERAGE);
        material->setDampingCombineMode(PxCombineMode::eAVERAGE);
    }
    materials.emplace(key, material);
    return *material;
}

void releaseMaterials(MaterialCache &materials) {
    for (auto &[key, material] : materials)
        material->release();
    materials.clear();
}

PxVec3 inertia(const Rigid &r) {
    // Match the existing Bullet bake's sphere/box inertia and bounding-box capsule approximation.
    if (r.shape == 0)
        return PxVec3(0.4f * r.mass * r.size.x * r.size.x * scale * scale);
    PxVec3 h = r.shape == 1 ? r.size : PxVec3(r.size.x, r.size.x + r.size.y / 2, r.size.x);
    h *= scale;
    return PxVec3(h.y * h.y + h.z * h.z, h.x * h.x + h.z * h.z, h.x * h.x + h.y * h.y) * (r.mass / 3);
}
float damping(float authored, float dt) {
    return (1 - std::pow(1 - std::clamp(authored, 0.0f, 1.0f), dt)) / dt;
}
void softenAngularLimit(PxJointLimitParameters &limit, float effectiveInertia, const Options &o) {
    // Finite, critically damped limit response. This is an explicit prototype
    // option, not an equivalence claim for Bullet STOP_ERP. Locked axes stay locked.
    const float omega = 2 * pi * o.angularLimitHz;
    limit.stiffness = effectiveInertia * omega * omega;
    limit.damping = 2 * effectiveInertia * omega;
}
PxD6Motion::Enum motion(float lo, float hi) {
    return lo > hi ? PxD6Motion::eFREE : lo == hi ? PxD6Motion::eLOCKED : PxD6Motion::eLIMITED;
}
void assemble(Instance &inst, PxPhysics &physics, PxScene &world, MaterialCache &materials, const Options &o, int id,
              float dt) {
    auto &sc = inst.sc;
    for (int index : o.contactRigids)
        require(size_t(index) < sc.rigids.size(), "compliant rigid index outside source scene");
    for (int index : o.angularLimitRigids)
        require(size_t(index) < sc.rigids.size(), "angular limit rigid index outside source scene");
    inst.bodies.reserve(sc.rigids.size());
    inst.targets.resize(sc.rigids.size());
    inst.bodyDepth.assign(sc.rigids.size(), 0.f);
    for (size_t i = 0; i < sc.rigids.size(); ++i) {
        const auto &r = sc.rigids[i];
        Body b;
        b.mode = r.mode;
        b.bone = r.bone;
        b.rest = PxTransform(r.pos * scale + inst.origin, eulerYXZ(r.rot));
        b.offset = PxTransform((r.pos - (r.bone >= 0 ? sc.bones[r.bone].pos : PxVec3(0))) * scale, b.rest.q);
        const bool compliant = o.contactFrequencyHz > 0 &&
            (o.contactRigids.empty() || std::binary_search(o.contactRigids.begin(), o.contactRigids.end(), int(i)));
        auto &m = materialFor(physics, materials, r, o, compliant);
        PxShape *shape = nullptr;
        if (r.shape == 0)
            shape = physics.createShape(PxSphereGeometry(r.size.x * scale), m, true);
        if (r.shape == 1)
            shape = physics.createShape(PxBoxGeometry(r.size * scale), m, true);
        if (r.shape == 2)
            shape = physics.createShape(PxCapsuleGeometry(r.size.x * scale, r.size.y * scale / 2), m, true);
        require(shape != nullptr, "shape creation failed");
        if (r.shape == 2)
            shape->setLocalPose(PxTransform(PxQuat(pi / 2, PxVec3(0, 0, 1)))); // PhysX X-axis -> PMX Y-axis
        shape->setContactOffset(o.contactOffset * scale);
        shape->setRestOffset(0);
        shape->setSimulationFilterData(
            PxFilterData(1u << r.group, PxU32(r.mask), PxU32(id), !o.contacts.empty() || o.depthDepen));
        b.actor = physics.createRigidDynamic(b.rest);
        require(b.actor != nullptr, "body creation failed");
        b.actor->attachShape(*shape);
        shape->release();
        b.actor->userData = reinterpret_cast<void *>(uintptr_t(i + 1));
        if (r.mode == 0)
            b.actor->setRigidBodyFlag(PxRigidBodyFlag::eKINEMATIC, true);
        else {
            b.actor->setMass(r.mass);
            b.actor->setMassSpaceInertiaTensor(inertia(r));
            b.actor->setLinearDamping(damping(r.linDamp, dt));
            b.actor->setAngularDamping(damping(r.angDamp, dt));
            b.actor->setSleepThreshold(0);
            b.actor->setStabilizationThreshold(0);
            // Bullet caps integrated rotation to pi/4 per substep. Bound the new
            // solver's angular speed on that scale, and cap penetration correction
            // separately so initial intersections cannot inject unbounded energy.
            b.actor->setMaxAngularVelocity(pi / (4 * dt));
            b.actor->setMaxDepenetrationVelocity(o.maxDepenetration * scale);
            b.actor->setSolverIterationCounts(PxU32(o.iters), PxU32(o.velocityIters));
        }
        world.addActor(*b.actor);
        inst.bodies.push_back(b);
    }
    for (size_t ji = 0; ji < sc.joints.size(); ++ji) {
        const auto &j = sc.joints[ji];
        if (j.a < 0 || j.b < 0 || j.a == j.b) {
            ++inst.skippedInvalid;
            continue;
        }
        auto &a = inst.bodies[j.a];
        auto &b = inst.bodies[j.b];
        if (a.mode == 0 && b.mode == 0) {
            ++inst.skippedStatic;
            continue;
        }
        PxTransform jw(j.pos * scale + inst.origin, eulerYXZ(j.rot));
        PxVec3 effectiveInertia(0);
        const bool selectedAngular = !o.angularLimitRigids.empty() && a.mode != 0 && b.mode != 0 &&
            std::binary_search(o.angularLimitRigids.begin(), o.angularLimitRigids.end(), j.a) &&
            std::binary_search(o.angularLimitRigids.begin(), o.angularLimitRigids.end(), j.b);
        const bool softenJoint = o.angularLimitHz > 0 && j.soft &&
            (o.angularLimitRigids.empty() || selectedAngular);
        bool softened = false;
        if (softenJoint) {
            for (int k = 0; k < 3; ++k) {
                PxVec3 axis(0);
                axis[k] = 1;
                axis = jw.q.rotate(axis);
                float response = 0;
                for (int index : {j.a, j.b}) {
                    if (inst.bodies[index].mode == 0)
                        continue;
                    auto local = inst.bodies[index].rest.q.rotateInv(axis);
                    auto tensor = inertia(sc.rigids[index]);
                    for (int dim = 0; dim < 3; ++dim)
                        response += local[dim] * local[dim] / tensor[dim];
                }
                require(response > 0 && std::isfinite(response), "invalid angular limit inertia");
                effectiveInertia[k] = 1 / response;
            }
        }
        auto fa = a.rest.transformInv(jw), fb = b.rest.transformInv(jw);
        inst.jointFrames.push_back({ji, fa, fb});
        if (j.twistZLimit >= 0) {
            auto *antiTwist = TwistZConstraint::create(physics, a.actor, b.actor, fa, fb,
                                                       j.twistZLimit, o.disablePreprocessing);
            inst.twistZLimits.push_back({antiTwist, ji, j.twistZLimit});
            if (j.twistZLimit == 0) ++inst.twistZLockedJoints;
        }
        PxVec3 shiftP(0), shiftA(0), lo(0), hi(0);
        for (int k = 0; k < 3; ++k) {
            if (j.linLo[k] == j.linHi[k])
                shiftP[k] = j.linLo[k] * scale;
            // Bullet extracts Euler angles from the TRANSPOSE of A^-1 B (see btGetMatrixElem).
            lo[k] = -j.angHi[k];
            hi[k] = -j.angLo[k];
            if (lo[k] == hi[k]) {
                shiftA[k] = lo[k];
                lo[k] = hi[k] = 0;
            }
        }
        PxTransform shift(shiftP, eulerXYZ(shiftA));
        if (shiftP.magnitudeSquared() > 0 || shiftA.magnitudeSquared() > 0)
            ++inst.shiftedLocks;
        // Nonzero fixed angles change the frame; remaining axes are a documented D6 approximation.
        fa = fa * shift;
        auto *c = PxD6JointCreate(physics, a.actor, fa, b.actor, fb);
        require(c != nullptr, "D6 joint creation failed");
        c->setConstraintFlag(PxConstraintFlag::eCOLLISION_ENABLED, true);
        if (o.disablePreprocessing)
            c->setConstraintFlag(PxConstraintFlag::eDISABLE_PREPROCESSING, true);
        c->setDrivePosition(shift.getInverse());
        const bool responseSlerp = j.response && j.angularHz > 0 &&
            motion(lo.x, hi.x) != PxD6Motion::eLOCKED &&
            motion(lo.y, hi.y) != PxD6Motion::eLOCKED &&
            motion(lo.z, hi.z) != PxD6Motion::eLOCKED;
        c->setAngularDriveConfig(responseSlerp ? PxD6AngularDriveConfig::eSLERP : PxD6AngularDriveConfig::eSWING_TWIST);
        if (j.response) {
            ++inst.responseJoints;
            if (responseSlerp) ++inst.responseSlerpJoints;
            else if (j.angularHz > 0) ++inst.responseAxisJoints;
        }
        for (int k = 0; k < 3; ++k) {
            auto lm = motion(j.linLo[k], j.linHi[k]);
            c->setMotion(PxD6Axis::Enum(k), lm);
            if (lm == PxD6Motion::eLIMITED)
                c->setLinearLimit(PxD6Axis::Enum(k),
                                  PxJointLinearLimitPair(physics.getTolerancesScale(), j.linLo[k] * scale,
                                                         j.linHi[k] * scale));
            c->setMotion(PxD6Axis::Enum(k + 3), motion(lo[k], hi[k]));
            if (j.response && j.linearHz > 0 && j.sprLin[k] > 0 && lm != PxD6Motion::eLOCKED) {
                const float omega = 2 * pi * j.linearHz;
                c->setDrive(PxD6Drive::Enum(k), PxD6JointDrive(omega * omega,
                    2 * j.linearRatio * omega, PX_MAX_F32, true));
            } else if (j.sprLin[k] > 0)
                c->setDrive(PxD6Drive::Enum(k),
                            PxD6JointDrive(j.sprLin[k], o.driveDamping, PX_MAX_F32, false));
            const PxD6Drive::Enum drives[] = {PxD6Drive::eTWIST, PxD6Drive::eSWING1, PxD6Drive::eSWING2};
            if (j.response && j.angularHz > 0 && !responseSlerp && motion(lo[k], hi[k]) != PxD6Motion::eLOCKED) {
                const float omega = 2 * pi * j.angularHz;
                c->setDrive(drives[k], PxD6JointDrive(0,
                    2 * j.angularRatio * omega + omega * omega * dt, PX_MAX_F32, true));
            } else if (!j.response && j.sprAng[k] > 0)
                c->setDrive(drives[k], PxD6JointDrive(j.sprAng[k] * scale * scale,
                                                      o.driveDamping * scale * scale, PX_MAX_F32, false));
        }
        if (j.response && j.angularHz > 0) {
            const float omega = 2 * pi * j.angularHz;
            // The SDK position SLERP Jacobian becomes singular at 180 deg.
            // Use a shortest-arc quaternion-log spring with the SDK's regular
            // world-axis velocity rows. These rows retain effective-inertia
            // compensation and equal/opposite constraint impulses.
            // Implicit Euler: v'=(v-k*dt*error)/(1+c*dt+k*dt*dt).
            const float k = omega * omega;
            const float damping = 2 * j.angularRatio * omega + k * dt;
            if (responseSlerp)
                c->setDrive(PxD6Drive::eSLERP, PxD6JointDrive(0,
                    damping, PX_MAX_F32, true));
            PxVec3 active(0);
            for (int axis = 0; axis < 3; ++axis)
                active[axis] = motion(lo[axis], hi[axis]) == PxD6Motion::eLOCKED ? 0.f : 1.f;
            inst.responseDrives.push_back({c, j.a, j.b, fa, fb, shift.getInverse().q, k / damping, active, responseSlerp});
        }
        if (motion(lo.x, hi.x) == PxD6Motion::eLIMITED) {
            PxJointAngularLimitPair limits(lo.x, hi.x);
            if (softenJoint && (selectedAngular || hi.x - lo.x > pi / 30)) {
                softenAngularLimit(limits, effectiveInertia.x, o);
                softened = true;
            }
            require(limits.isValid(), "twist limits outside PhysX range");
            c->setTwistLimit(limits);
        }
        auto my = motion(lo.y, hi.y), mz = motion(lo.z, hi.z);
        if (my == PxD6Motion::eLIMITED || mz == PxD6Motion::eLIMITED) {
            require(my != PxD6Motion::eFREE && mz != PxD6Motion::eFREE,
                    "mixed free/limited swing requires custom mapping");
            PxJointLimitPyramid limits(lo.y, hi.y, lo.z, hi.z);
            // Pyramid uses one spring for both swing axes. Preserve narrow and
            // mixed locked/limited swing configurations as hard limits.
            if (softenJoint && my == PxD6Motion::eLIMITED && mz == PxD6Motion::eLIMITED &&
                (selectedAngular || (hi.y - lo.y > pi / 30 && hi.z - lo.z > pi / 30))) {
                softenAngularLimit(limits, std::min(effectiveInertia.y, effectiveInertia.z), o);
                softened = true;
            }
            require(limits.isValid(), "swing limits outside PhysX range");
            c->setPyramidSwingLimit(limits);
        }
        inst.compliantAngularJoints += softened;
    }
    require(o.angularLimitRigids.empty() || inst.compliantAngularJoints > 0,
            "angular limit selection matched no limited dynamic internal joints");
    for (auto &b : inst.bodies) {
        auto p = o.intro > 0 ? b.rest : inst.frames.bone(0, b.bone) * b.offset;
        if (o.intro == 0)
            p.p += inst.origin;
        b.actor->setGlobalPose(p);
        b.previous = p;
    }
    inst.startRecord = o.intro + o.introHold + (inst.loop ? o.warmup * inst.frames.F : o.settle);
    // Leave the last half untouched so it remains the actual predecessor of
    // the continuation used at the beginning. Tiny clips have no blend window.
    inst.recorded = inst.frames.F + (inst.loop && inst.frames.F >= 4 ? std::min(o.extra, inst.frames.F / 2) : 0);
    inst.total = inst.startRecord + inst.recorded;
    inst.output.resize(size_t(inst.recorded) * sc.rigids.size() * 7);
    const float duration = inst.frames.F / sc.fps;
    std::vector<bool> locked(sc.rigids.size(),false),added(sc.rigids.size(),false);
    for(const auto &lock:sc.boneTwists) locked[lock.child]=true;
    for(const auto &lock:sc.boneTwists) {
        require(lock.parent<0 || !locked[lock.parent] || added[lock.parent],"bone_twist records must be parent-first");
        auto &child=inst.bodies[lock.child];
        PxRigidDynamic *reference=nullptr;
        PxTransform referenceRest(PxIdentity);
        if(lock.parent>=0) { reference=inst.bodies[lock.parent].actor;referenceRest=inst.bodies[lock.parent].rest; }
        else {
            referenceRest=PxTransform(sc.bones[lock.parentBone].pos*scale+inst.origin);
            reference=physics.createRigidDynamic(referenceRest);
            require(reference!=nullptr,"bone_twist reference allocation failed");
            reference->setRigidBodyFlag(PxRigidBodyFlag::eKINEMATIC,true);
            world.addActor(*reference);
        }
        const PxTransform frameA(PxVec3(0),referenceRest.q.getConjugate()*lock.frame);
        const PxTransform frameB(PxVec3(0),child.rest.q.getConjugate()*lock.frame);
        auto *constraint=TwistZConstraint::create(physics,reference,child.actor,frameA,frameB,0,o.disablePreprocessing);
        inst.boneLocks.push_back({constraint,lock.child,lock.parent,lock.parentBone,reference,frameA,frameB,referenceRest,referenceRest});
        added[lock.child]=true;
    }
    inst.windCycles = std::max(1, int(std::lround(duration / o.windPeriod)));
    inst.windSideCycles = std::max(inst.windCycles + 1, int(std::lround(inst.windCycles * 1.618f)));
    // A sub-second clip must not turn a gentle breeze into rapid flutter.
    inst.windGustGain = inst.loop ? std::pow(std::min(1.0f, duration / o.windPeriod), 2.0f) : 1;
}
PxVec3 windAt(const Instance &inst, const Options &o, double frame) {
    const double phase = inst.loop ? frame / inst.frames.F : frame / (inst.sc.fps * o.windPeriod);
    const double mainPhase = 2 * 3.14159265358979323846 * (inst.loop ? inst.windCycles : 1) * phase;
    const double sidePhase = 2 * 3.14159265358979323846 * (inst.loop ? inst.windSideCycles : 1.618) * phase;
    auto dir = o.windDir.getNormalized();
    auto side = dir.cross(PxVec3(0, 1, 0));
    if (side.magnitudeSquared() < 1e-6f)
        side = PxVec3(0, 0, 1);
    side.normalize();
    const float main = o.windBase + (1 - o.windBase) * inst.windGustGain * float(std::sin(mainPhase + 0.7));
    const float lateral = o.windTurb * inst.windGustGain * float(std::sin(sidePhase + 2.1));
    return (dir * main + side * lateral) * (o.wind * scale);
}
void targets(Instance &inst, const Options &o, int tick) {
    int after = tick - o.intro - o.introHold;
    int frame = after < 0                 ? 0
                : tick < inst.startRecord ? (inst.loop ? after % inst.frames.F : 0)
                                          : (tick - inst.startRecord) % inst.frames.F;
    for (size_t i = 0; i < inst.bodies.size(); ++i) {
        auto &b = inst.bodies[i];
        auto t = inst.frames.bone(frame, b.bone) * b.offset;
        t.p += inst.origin;
        inst.targets[i] = tick < o.intro ? lerp(b.rest, t, float(tick + 1) / o.intro) : t;
    }
    for(auto &lock:inst.boneLocks) if(lock.parent<0) {
        auto t=inst.frames.bone(frame,lock.parentBone);t.p+=inst.origin;
        const PxTransform rest(inst.sc.bones[lock.parentBone].pos*scale+inst.origin);
        lock.target=tick<o.intro?lerp(rest,t,float(tick+1)/o.intro):t;
    }
    inst.frameContacts.clear();
}
void enforceBoneTwist(Instance &inst,int tick) {
    // Position-level enforcement is part of the simulation, before the next
    // contacts/forces. It never touches output poses or animation channels.
    // Each correction removes only right-hand local-Z twist: the bone's
    // world direction (two swing freedoms) and its center/linear velocity stay.
    for(auto &lock:inst.boneLocks) {
        auto *child=inst.bodies[lock.child].actor;
        const auto a=lock.reference->getGlobalPose()*lock.frameA;
        auto pose=child->getGlobalPose();
        const auto directionBefore=(pose.q*lock.frameB.q).rotate(PxVec3(0,0,1));
        PxQuat q=(a.q.getConjugate()*pose.q*lock.frameB.q).getNormalized();
        const float before=lock.joint->lockError();
        inst.maxBoneTwistBefore=std::max(inst.maxBoneTwistBefore,double(before*180/pi));
        const float den=std::sqrt(q.z*q.z+q.w*q.w);
        // At antiparallel swing, twist is undefined. Do not turn float noise
        // into a large axial correction; q.z already within 1e-7 is locked.
        if(den>1e-6f && std::abs(q.z)>1e-7f) {
            const PxQuat twist(0,0,q.z/den,q.w/den);
            const PxQuat swing=(q*twist.getConjugate()).getNormalized();
            const double correction=std::abs(double(principalTwistZ(q))*180/pi);
            inst.boneTwistCorrectionsOver1+=correction>1;
            inst.boneTwistCorrectionsOver10+=correction>10;
            inst.boneTwistCorrectionsOver45+=correction>45;
            if(correction>inst.maxBoneTwistCorrection){inst.maxBoneTwistCorrection=correction;inst.maxBoneTwistCorrectionChild=lock.child;inst.maxBoneTwistCorrectionTick=tick;}
            if(tick>=inst.startRecord&&correction>inst.maxBoneTwistCorrectionRecorded){
                inst.maxBoneTwistCorrectionRecorded=correction;inst.maxBoneTwistCorrectionRecordedChild=lock.child;
                inst.maxBoneTwistCorrectionRecordedFrame=tick-inst.startRecord;
            }
            pose.q=(a.q*swing*lock.frameB.q.getConjugate()).getNormalized();
            child->setGlobalPose(pose,false);
            q=swing;
        }
        const auto directionAfter=(pose.q*lock.frameB.q).rotate(PxVec3(0,0,1));
        inst.maxBoneSwingDirectionDelta=std::max(inst.maxBoneSwingDirectionDelta,double((directionBefore-directionAfter).magnitude()));
        // Velocity-level tangent projection prevents the forbidden spin from
        // accumulating kinetic energy and being removed again next substep.
        PxVec3 axis=a.q.rotate(PxVec3(q.y,-q.x,q.w).getNormalized());
        auto omega=child->getAngularVelocity();
        const auto parentOmega=lock.reference->getAngularVelocity();
        omega-=axis*axis.dot(omega-parentOmega);
        child->setAngularVelocity(omega,false);
        const float after=lock.joint->lockError();
        require(std::isfinite(after)&&after<0.00035f,"bone local-Z lock enforcement failed");
        inst.maxBoneTwistAfter=std::max(inst.maxBoneTwistAfter,double(after*180/pi));
    }
}
void measureBoneTwistWithoutProjection(Instance &inst) {
    for(const auto &lock:inst.boneLocks) {
        const float error=lock.joint->lockError();
        require(std::isfinite(error),"non-finite bone local-Z lock residual");
        const double degrees=double(error*180/pi);
        inst.maxBoneTwistBefore=std::max(inst.maxBoneTwistBefore,degrees);
        inst.maxBoneTwistAfter=std::max(inst.maxBoneTwistAfter,degrees);
    }
}
PxVec3 bulletAngles(PxQuat q) {
    // Same matrix entries as Bullet matrixToEulerXYZ (transpose convention).
    PxMat33 m(q);
    float sy = std::clamp(m(2, 0), -1.0f, 1.0f);
    if (std::abs(sy) < 0.999999f)
        return PxVec3(std::atan2(-m(2, 1), m(2, 2)), std::asin(sy), std::atan2(-m(1, 0), m(0, 0)));
    return PxVec3(sy > 0 ? std::atan2(m(0, 1), m(1, 1)) : -std::atan2(m(0, 1), m(1, 1)), std::asin(sy), 0);
}
float limitError(float v, float lo, float hi) {
    return lo > hi ? 0 : std::max({lo - v, v - hi, 0.0f});
}
void record(Instance &inst, int tick) {
    if (tick < inst.startRecord)
        return;
    int frame = tick - inst.startRecord;
    for (const auto &limit : inst.twistZLimits) {
        const float angle = std::abs(limit.joint->angle());
        require(std::isfinite(angle), "non-finite twist_z angle");
        inst.maxTwistZAngle = std::max(inst.maxTwistZAngle, double(angle * 180 / pi));
        inst.maxTwistZError = std::max(inst.maxTwistZError,
            double(std::max(0.f, angle - limit.limit) * 180 / pi));
        if (limit.limit == 0)
            inst.maxTwistZLockError = std::max(inst.maxTwistZLockError,
                double(limit.joint->lockError() * 180 / pi));
    }
    for (size_t i = 0; i < inst.bodies.size(); ++i) {
        auto t = inst.bodies[i].actor->getGlobalPose();
        require(t.isFinite(), "non-finite output pose");
        auto p = (t.p - inst.origin) / scale;
        float *dst = &inst.output[(size_t(frame) * inst.bodies.size() + i) * 7];
        dst[0] = p.x;
        dst[1] = p.y;
        dst[2] = p.z;
        dst[3] = t.q.x;
        dst[4] = t.q.y;
        dst[5] = t.q.z;
        dst[6] = t.q.w;
    }
    for (const auto &jf : inst.jointFrames) {
        const auto &j = inst.sc.joints[jf.index];
        auto a = inst.bodies[j.a].actor->getGlobalPose() * jf.a,
             b = inst.bodies[j.b].actor->getGlobalPose() * jf.b;
        auto rel = a.transformInv(b);
        auto angles = bulletAngles(rel.q);
        for (int k = 0; k < 3; ++k) {
            inst.maxAngleError = std::max(inst.maxAngleError,
                                          double(limitError(angles[k], j.angLo[k], j.angHi[k]) * 180 / pi));
            const double linearError = limitError(rel.p[k] / scale, j.linLo[k], j.linHi[k]);
            if (linearError > inst.maxLinearError) {
                inst.maxLinearError = linearError;
                inst.maxLinearJoint = int(jf.index);
                inst.maxLinearFrame = frame;
                inst.maxLinearAxis = k;
            }
        }
    }
    for (const auto &p : inst.frameContacts) {
        auto &v = inst.contacts[p.first];
        ++v.first;
        v.second = std::max(v.second, p.second);
    }
}
int run(const std::vector<std::string> &args) {
    auto started = Clock::now();
    Options o = parse(args);
    std::vector<Instance> instances;
    auto add = [&](fs::path scene, fs::path frames, fs::path out, bool loop) {
        Instance inst;
        inst.sc = loadScene(scene);
        inst.frames = loadFrames(frames);
        inst.out = out;
        inst.loop = loop;
        require(inst.frames.NB == int(inst.sc.bones.size()), "scene/frame bone count mismatch");
        if (o.overrideGravity)
            inst.sc.gravity = o.gravity;
        for (const auto &existing : instances)
            require(fs::absolute(out) != fs::absolute(existing.out), "duplicate batch output");
        instances.push_back(std::move(inst));
    };
    if (o.batch.empty())
        add(o.positional[0], o.positional[1], o.positional[2], o.loop);
    else {
        std::ifstream in(o.batch);
        require(bool(in), "cannot open batch manifest");
        std::string line;
        while (std::getline(in, line)) {
            if (!line.empty() && line.back() == '\r')
                line.pop_back();
            if (line.empty() || line[0] == '#')
                continue;
            std::vector<std::string> fields;
            std::istringstream s(line);
            std::string field;
            while (std::getline(s, field, '\t'))
                fields.push_back(field);
            require(fields.size() == 4 && (fields[3] == "0" || fields[3] == "1"),
                    "batch row must be scene<TAB>frames<TAB>output<TAB>loop");
            add(fs::u8path(fields[0]), fs::u8path(fields[1]), fs::u8path(fields[2]), fields[3] == "1");
        }
    }
    require(!instances.empty() && instances.size() <= 1024, "invalid batch size (1..1024)");
    float fps = instances[0].sc.fps;
    int substeps = int(std::ceil(o.hz / fps));
    float dt = 1 / (fps * substeps);
    for (const auto &i : instances)
        require(i.sc.fps == fps && i.sc.gravity == instances[0].sc.gravity,
                "batch requires common fps and gravity");
    PxDefaultAllocator allocator;
    Errors errors;
    auto *foundation = PxCreateFoundation(PX_PHYSICS_VERSION, allocator, errors);
    require(foundation, "foundation creation failed");
    PxTolerancesScale tolerances;
    tolerances.length = 1;
    tolerances.speed = 10;
    auto *physics = PxCreatePhysics(PX_PHYSICS_VERSION, *foundation, tolerances);
    require(physics, "physics creation failed");
    PxInitExtensions(*physics, nullptr);
    auto *dispatcher = PxDefaultCpuDispatcherCreate(o.threads);
    PxCudaContextManager *cuda = nullptr;
    if (o.gpu) {
        PxCudaContextManagerDesc cd;
        std::cout << "[moe_physx] Creating CUDA context\n";
        cuda = PxCreateCudaContextManager(*foundation, cd, PxGetProfilerCallback());
        require(cuda && cuda->contextIsValid(),
                "GPU requested but CUDA context unavailable; refusing CPU fallback");
        std::cout << "[moe_physx] GPU: " << cuda->getDeviceName() << "\n";
    }
    ContactCallback callback;
    callback.instances = &instances;
    PxSceneDesc sd(tolerances);
    sd.gravity = PxVec3(0, instances[0].sc.gravity * scale, 0);
    sd.cpuDispatcher = dispatcher;
    sd.filterShader = filter;
    sd.solverType = o.tgs ? PxSolverType::eTGS : PxSolverType::ePGS;
    sd.simulationEventCallback = &callback;
    sd.flags |= PxSceneFlag::eENABLE_PCM;
    if (o.externalForcesEveryIteration)
        sd.flags |= PxSceneFlag::eENABLE_EXTERNAL_FORCES_EVERY_ITERATION_TGS;
    if (o.gpu) {
        sd.cudaContextManager = cuda;
        sd.flags |= PxSceneFlag::eENABLE_GPU_DYNAMICS;
        sd.broadPhaseType = PxBroadPhaseType::eGPU;
        // Fixed GPU buffers do not grow when independent timelines are batched.
        // Keep SDK defaults for small scenes and reserve per-actor headroom for
        // dense PMX overlaps. SDK overflow warnings still reject the whole solve.
        size_t bodyCount = 0;
        for (const auto &inst : instances)
            bodyCount += inst.sc.rigids.size();
        require(bodyCount <= size_t(PX_MAX_U32) / 32, "GPU actor budget exceeds buffer index range");
        auto &memory = sd.gpuDynamicsConfig;
        memory.foundLostPairsCapacity = std::max(memory.foundLostPairsCapacity, PxU32(bodyCount * 32));
        // Dense cloth candidates exceed the old linear sizing heuristic.
        // Bound found+lost pairs by the per-instance all-pairs maximum;
        // independent instances are spatially separated and collision-filtered.
        PxU64 candidatePairs = 0;
        bool hasResponse = false;
        for (const auto &inst : instances) {
            hasResponse |= std::any_of(inst.sc.joints.begin(), inst.sc.joints.end(),
                                      [](const Joint &j) { return j.response; });
            const PxU64 n = inst.sc.rigids.size();
            candidatePairs += n * (n - 1);
        }
        if (hasResponse) {
            require(candidatePairs <= PX_MAX_U32, "candidate broadphase capacity overflow");
            memory.foundLostPairsCapacity = std::max(memory.foundLostPairsCapacity, PxU32(candidatePairs));
        }
        memory.maxRigidContactCount = std::max(memory.maxRigidContactCount, PxU32(bodyCount * 16));
        memory.maxRigidPatchCount = std::max(memory.maxRigidPatchCount, PxU32(bodyCount * 8));
    }
    auto *world = physics->createScene(sd);
    require(world, "scene creation failed");
    require(world->getSolverType() == sd.solverType, "requested constraint solver was not enabled");
    if (o.gpu)
        require(world->getFlags().isSet(PxSceneFlag::eENABLE_GPU_DYNAMICS) &&
                    world->getBroadPhaseType() == PxBroadPhaseType::eGPU,
                "GPU scene flags unavailable; refusing CPU fallback");
    std::cout << "[moe_physx] Scene created\n";
    int ticks = 0;
    size_t actors = 0;
    MaterialCache materials;
    float spacing = 1;
    for (const auto &inst : instances)
        for (const auto &r : inst.sc.rigids)
            spacing = std::max(spacing, (r.pos.magnitude() + r.size.magnitude()) * scale * 4);
    int gridWidth = std::max(8, int(std::ceil(std::sqrt(double(instances.size())))));
    for (size_t i = 0; i < instances.size(); ++i) {
        auto &inst = instances[i];
        inst.origin = PxVec3(float(i % gridWidth) * spacing, 0, float(i / gridWidth) * spacing);
        assemble(inst, *physics, *world, materials, o, int(i), dt);
        ticks = std::max(ticks, inst.total);
        actors += inst.bodies.size();
    }
    errors.check();
    double setup = seconds(started);
    std::cout << "[moe_physx] Starting simulation, unique materials=" << materials.size() << "\n";
    auto simulated = Clock::now();
    for (int tick = 0; tick < ticks; ++tick) {
        if (tick % std::max(1, ticks / 10) == 0)
            std::cout << "[moe_physx] Progress " << tick << '/' << ticks << " animation ticks\n";
        for (auto &inst : instances)
            if (tick < inst.total) {
                if (o.recordDepenetration > 0 && tick == inst.startRecord)
                    for (auto &b : inst.bodies)
                        if (b.mode != 0)
                            b.actor->setMaxDepenetrationVelocity(o.recordDepenetration * scale);
                targets(inst, o, tick);
            }
        for (int sub = 0; sub < substeps; ++sub) {
            float alpha = float(sub + 1) / substeps;
            for (auto &inst : instances)
                if (tick < inst.total) {
                    // Continuous time, including intro/settle, is anchored at
                    // this instance's record start. Apply at each substep midpoint.
                    const auto wind = o.wind == 0 ? PxVec3(0) :
                        windAt(inst, o, double(tick - inst.startRecord) + (sub + 0.5) / substeps);
                    for (size_t bi = 0; bi < inst.bodies.size(); ++bi) {
                        auto &b = inst.bodies[bi];
                        if (b.mode == 0)
                            b.actor->setKinematicTarget(lerp(b.previous, inst.targets[bi], alpha));
                        else if (o.wind != 0)
                            b.actor->addForce(wind, o.windAccel ? PxForceMode::eACCELERATION : PxForceMode::eFORCE);
                    }
                    for(auto &lock:inst.boneLocks) if(lock.parent<0)
                        lock.reference->setKinematicTarget(lerp(lock.previous,lock.target,alpha));
                    for (const auto &drive : inst.responseDrives) {
                        const PxTransform a = inst.bodies[drive.a].actor->getGlobalPose() * drive.frameA;
                        const PxTransform b = inst.bodies[drive.b].actor->getGlobalPose() * drive.frameB;
                        PxQuat error = (a.q.getConjugate() * b.q * drive.target.getConjugate()).getNormalized();
                        if (error.w < 0) error = -error;
                        const PxVec3 imag = error.getImaginaryPart();
                        const float n = imag.magnitude();
                        const PxVec3 logError = n > 1e-7f ? imag * (2 * std::atan2(n, error.w) / n) : imag * 2;
                        // SDK SWING/TWIST rows use the opposite velocity-target
                        // convention to SLERP. Do not drive authored locked axes.
                        const PxVec3 velocity = logError.multiply(drive.active) * drive.velocityGain;
                        drive.joint->setDriveVelocity(PxVec3(0), drive.slerp ? -velocity : velocity);
                    }
                }
            if (o.depthDepen)
                for (auto &inst : instances)
                    std::fill(inst.bodyDepth.begin(), inst.bodyDepth.end(), 0.f);
            world->simulate(dt);
            require(world->fetchResults(true), "fetchResults failed");
            errors.check();
            require(!callback.invalidInstance, "invalid or cross-instance contact");
            if (o.depthDepen)
                for (auto &inst : instances)
                    if (tick < inst.total)
                        for (size_t bi = 0; bi < inst.bodies.size(); ++bi) {
                            auto &b = inst.bodies[bi];
                            if (b.mode == 0)
                                continue;
                            const float d = inst.bodyDepth[bi];
                            const float t = std::clamp((d - o.depthLo) / (o.depthHi - o.depthLo), 0.f, 1.f);
                            const float v = o.depthVelLo + t * (o.maxDepenetration - o.depthVelLo);
                            b.actor->setMaxDepenetrationVelocity(v * scale);
                            if (d > 0 && tick >= inst.startRecord) {
                                inst.depthDepenSum += v;
                                ++inst.depthDepenSamples;
                                inst.depthDepenAtMax += t >= 1;
                            }
                        }
            for(auto &inst:instances) if(tick<inst.total) {
                if(o.boneTwistProjection) enforceBoneTwist(inst,tick);
                else measureBoneTwistWithoutProjection(inst);
            }
        }
        for (auto &inst : instances)
            if (tick < inst.total) {
                record(inst, tick);
                for (size_t bi = 0; bi < inst.bodies.size(); ++bi)
                    inst.bodies[bi].previous = inst.targets[bi];
                for(auto &lock:inst.boneLocks) lock.previous=lock.target;
                if (tick + 1 == inst.total) {
                    for (auto &b : inst.bodies)
                        world->removeActor(*b.actor);
                    for(auto &lock:inst.boneLocks) if(lock.parent<0) world->removeActor(*lock.reference);
                    inst.removed = true;
                }
            }
    }
    double sim = seconds(simulated);
    errors.check();
    for (auto &inst : instances) {
        if (!inst.out.parent_path().empty())
            fs::create_directories(inst.out.parent_path());
        std::ofstream out(inst.out, std::ios::binary);
        int nr = int(inst.bodies.size());
        out.write(reinterpret_cast<char *>(&inst.recorded), 4);
        out.write(reinterpret_cast<char *>(&nr), 4);
        out.write(reinterpret_cast<const char *>(inst.output.data()),
                  std::streamsize(inst.output.size() * 4));
        require(bool(out), "output write failed");
        out.close();
        std::ofstream report(fs::path(inst.out.wstring() + L".solver.json"));
        report << std::setprecision(10) << "{\n  \"backend\": \"physx-" << (o.gpu ? "gpu" : "cpu")
               << "\",\n  \"sdk_version\": \"5.11.0\",\n  \"solver\": \"" << (o.tgs ? "TGS" : "PGS") << "\",\n"
               << "  \"angular_model\": \"swing-twist-approximation\",\n  \"angular_limit_sign_flipped\": "
                  "true,\n"
               << "  \"body_damping\": \"exponential-equivalent; no Bullet additional damping\",\n  "
                  "\"joint_limits\": \"PhysX hard locks; optional compliant wide angular limits; Bullet ERP not mapped\",\n"
               << "  \"angular_limit_hz\": " << o.angularLimitHz << ",\n"
               << "  \"angular_limit_rigid_indices\": [" << indicesText(o.angularLimitRigids) << "],\n"
               << "  \"compliant_angular_joints\": " << inst.compliantAngularJoints << ",\n"
               << "  \"response_joints\": " << inst.responseJoints << ",\n"
               << "  \"response_slerp_joints\": " << inst.responseSlerpJoints << ",\n"
               << "  \"response_axis_joints\": " << inst.responseAxisJoints << ",\n"
               << "  \"twist_z_joints\": " << inst.twistZLimits.size() << ",\n"
               << "  \"twist_z_locked_joints\": " << inst.twistZLockedJoints << ",\n"
               << "  \"bone_twist_locked_bodies\": " << inst.boneLocks.size() << ",\n"
               << "  \"bone_twist_post_projection_enabled\": " << (o.boneTwistProjection ? "true" : "false") << ",\n"
               << "  \"max_bone_twist_before_projection_deg\": " << inst.maxBoneTwistBefore << ",\n"
               << "  \"max_bone_twist_after_projection_deg\": " << inst.maxBoneTwistAfter << ",\n"
               << "  \"max_bone_twist_correction_deg\": " << inst.maxBoneTwistCorrection << ",\n"
               << "  \"max_bone_twist_correction_child\": " << inst.maxBoneTwistCorrectionChild << ",\n"
               << "  \"max_bone_twist_correction_tick\": " << inst.maxBoneTwistCorrectionTick << ",\n"
               << "  \"max_bone_twist_recorded_correction_deg\": " << inst.maxBoneTwistCorrectionRecorded << ",\n"
               << "  \"max_bone_twist_recorded_correction_child\": " << inst.maxBoneTwistCorrectionRecordedChild << ",\n"
               << "  \"max_bone_twist_recorded_correction_frame\": " << inst.maxBoneTwistCorrectionRecordedFrame << ",\n"
               << "  \"bone_twist_corrections_over_1deg\": " << inst.boneTwistCorrectionsOver1 << ",\n"
               << "  \"bone_twist_corrections_over_10deg\": " << inst.boneTwistCorrectionsOver10 << ",\n"
               << "  \"bone_twist_corrections_over_45deg\": " << inst.boneTwistCorrectionsOver45 << ",\n"
               << "  \"max_bone_swing_direction_delta\": " << inst.maxBoneSwingDirectionDelta << ",\n"
               << "  \"max_twist_z_lock_manifold_error_deg\": " << inst.maxTwistZLockError << ",\n"
               << "  \"twist_z_model\": \"authored local Z swing-twist exact normalized Jacobian; one angular DOF; no linear rows\",\n"
               << "  \"max_twist_z_angle_deg\": " << inst.maxTwistZAngle << ",\n"
               << "  \"max_twist_z_limit_error_deg\": " << inst.maxTwistZError << ",\n"
               << "  \"response_model\": \"quaternion-log implicit spring / acceleration velocity drive; unchanged angular bounds\",\n"
               << "  \"contact_frequency_hz\": " << o.contactFrequencyHz << ",\n"
               << "  \"contact_damping_ratio\": " << o.contactDampingRatio << ",\n"
               << "  \"contact_rigid_indices\": [" << o.contactRigidIndices << "],\n"
               << "  \"external_forces_every_iteration\": " << (o.externalForcesEveryIteration ? "true" : "false") << ",\n"
               << "  \"wind\": " << o.wind << ",\n  \"wind_accel\": " << (o.windAccel ? "true" : "false")
               << ",\n  \"wind_period_seconds\": " << o.windPeriod
               << ",\n  \"wind_base\": " << o.windBase << ",\n  \"wind_turb\": " << o.windTurb
               << ",\n  \"wind_direction\": [" << o.windDir.x << ", " << o.windDir.y << ", " << o.windDir.z << "]"
               << ",\n  \"wind_cycles\": " << inst.windCycles << ",\n  \"wind_side_cycles\": " << inst.windSideCycles
               << ",\n  \"wind_gust_gain\": " << inst.windGustGain
               << ",\n  \"loop_extra_frames\": " << inst.recorded - inst.frames.F << ",\n"
               << "  \"batch_instances\": " << instances.size() << ",\n  \"batch_actors\": " << actors
               << ",\n  \"batch_unique_materials\": " << materials.size()
               << ",\n  \"gpu_found_lost_pairs_capacity\": " << sd.gpuDynamicsConfig.foundLostPairsCapacity
               << ",\n  \"gpu_contact_capacity\": " << sd.gpuDynamicsConfig.maxRigidContactCount
               << ",\n  \"gpu_patch_capacity\": " << sd.gpuDynamicsConfig.maxRigidPatchCount
               << ",\n  \"frames\": " << inst.recorded << ",\n  \"bodies\": " << nr << ",\n"
               << "  \"skipped_kinematic_joints\": " << inst.skippedStatic
               << ",\n  \"skipped_invalid_joints\": " << inst.skippedInvalid
               << ",\n  \"shifted_fixed_joints\": " << inst.shiftedLocks << ",\n"
               << "  \"max_bullet_angle_limit_error_deg\": " << inst.maxAngleError
               << ",\n  \"max_linear_limit_error_pmx\": " << inst.maxLinearError << ",\n"
               << "  \"max_linear_error_joint\": " << inst.maxLinearJoint
               << ",\n  \"max_linear_error_frame\": " << inst.maxLinearFrame
               << ",\n  \"max_linear_error_axis\": " << inst.maxLinearAxis << ",\n"
               << "  \"actual_hz\": " << fps * substeps << ",\n  \"position_iterations\": " << o.iters
               << ",\n  \"velocity_iterations\": " << o.velocityIters << ",\n"
               << "  \"record_depenetration_velocity_pmx\": " << o.recordDepenetration << ",\n"
               << "  \"depth_depenetration\": " << (o.depthDepen ? "true" : "false") << ",\n"
               << "  \"depth_depenetration_lo_hi_vlo_pmx\": [" << o.depthLo << ", " << o.depthHi << ", " << o.depthVelLo << "],\n"
               << "  \"depth_depenetration_recorded_mean_velocity_pmx\": "
               << (inst.depthDepenSamples ? inst.depthDepenSum / inst.depthDepenSamples : 0) << ",\n"
               << "  \"depth_depenetration_recorded_at_max_fraction\": "
               << (inst.depthDepenSamples ? double(inst.depthDepenAtMax) / inst.depthDepenSamples : 0) << ",\n"
               << "  \"max_depenetration_velocity_pmx\": " << o.maxDepenetration
               << ",\n  \"max_angular_velocity\": " << pi / (4 * dt)
               << ",\n  \"disable_preprocessing\": " << (o.disablePreprocessing ? "true" : "false") << ",\n"
               << "  \"sdk_errors_or_warnings\": " << errors.count
               << ",\n  \"batch_setup_seconds\": " << setup
               << ",\n  \"batch_simulate_and_record_seconds\": " << sim << "\n}\n";
        require(bool(report), "report write failed");
        if (!o.contacts.empty()) {
            fs::path path =
                instances.size() == 1 ? o.contacts : fs::path(inst.out.wstring() + L".contacts.csv");
            std::ofstream contacts(path);
            contacts << "rigid_a,rigid_b,frames,max_depth\n";
            for (const auto &p : inst.contacts)
                contacts << p.first.first << ',' << p.first.second << ',' << p.second.first << ','
                         << p.second.second << '\n';
            require(bool(contacts), "contact report failed");
        }
    }
    std::cout << "[moe_physx] " << (o.tgs ? "TGS" : "PGS") << ' ' << (o.gpu ? "GPU" : "CPU") << " instances=" << instances.size()
              << " actors=" << actors << " setup=" << setup << "s simulation=" << sim
              << "s, angular model=explicit swing/twist approximation\n";
    world->release();
    releaseMaterials(materials);
    PxCloseExtensions();
    physics->release();
    dispatcher->release();
    if (cuda)
        cuda->release();
    foundation->release();
    return 0;
}
} // namespace
int wmain(int argc, wchar_t **argv) {
    std::cout << std::unitbuf;
    SetUnhandledExceptionFilter([](EXCEPTION_POINTERS *error) -> LONG {
        auto process = GetCurrentProcess();
        SymInitialize(process, nullptr, TRUE);
        CONTEXT context = *error->ContextRecord;
        STACKFRAME64 frame{};
        frame.AddrPC = {context.Rip, 0, AddrModeFlat};
        frame.AddrStack = {context.Rsp, 0, AddrModeFlat};
        frame.AddrFrame = {context.Rbp, 0, AddrModeFlat};
        std::cerr << "Native exception " << std::hex << error->ExceptionRecord->ExceptionCode << '\n';
        for (int i = 0; i < 30; ++i) {
            IMAGEHLP_MODULE64 module{};
            module.SizeOfStruct = sizeof(module);
            SymGetModuleInfo64(process, frame.AddrPC.Offset, &module);
            std::cerr << module.ModuleName << "+0x" << (frame.AddrPC.Offset - module.BaseOfImage) << '\n';
            alignas(SYMBOL_INFO) char storage[sizeof(SYMBOL_INFO) + MAX_SYM_NAME]{};
            auto *symbol = reinterpret_cast<SYMBOL_INFO *>(storage);
            symbol->SizeOfStruct = sizeof(SYMBOL_INFO);
            symbol->MaxNameLen = MAX_SYM_NAME;
            DWORD64 displacement = 0;
            if (SymFromAddr(process, frame.AddrPC.Offset, &displacement, symbol))
                std::cerr << symbol->Name << "+0x" << displacement << '\n';
            if (!StackWalk64(IMAGE_FILE_MACHINE_AMD64, process, GetCurrentThread(), &frame, &context,
                             nullptr, SymFunctionTableAccess64, SymGetModuleBase64, nullptr))
                break;
        }
        SymCleanup(process);
        return EXCEPTION_EXECUTE_HANDLER;
    });
    try {
        std::vector<std::string> args;
        for (int i = 0; i < argc; ++i) {
            int n = WideCharToMultiByte(CP_UTF8, 0, argv[i], -1, nullptr, 0, nullptr, nullptr);
            std::string s(size_t(n), 0);
            WideCharToMultiByte(CP_UTF8, 0, argv[i], -1, s.data(), n, nullptr, nullptr);
            s.pop_back();
            args.push_back(s);
        }
        return run(args);
    } catch (const std::exception &e) {
        std::cerr << "[moe_physx] " << e.what() << '\n';
        return 2;
    }
}
