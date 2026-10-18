// Included in the baker's anonymous namespace after PhysX declarations.
// One scalar swing-twist coordinate, theta=2 atan2(q.z,q.w), q=A^-1 B.
// Unlike the SDK D6 twist row (child's axis), this uses its exact gradient.
// Positive limits use two opposite inequalities in one DOF. A zero limit
// uses ONE bilateral equality q.z=0, preserving the two swing freedoms.
struct TwistZData {
    PxTransform frameA{PxIdentity}, frameB{PxIdentity};
    float limit = 0;
};

float principalTwistZ(const PxQuat &q) {
    return std::remainder(2 * std::atan2(q.z, q.w), 2 * pi);
}

PxU32 twistZPrep(Px1DConstraint *rows, PxVec3p &offset, PxU32 maxRows,
                  PxConstraintInvMassScale &massScale, const void *block,
                  const PxTransform &bodyA, const PxTransform &bodyB, bool,
                  PxVec3p &pointA, PxVec3p &pointB) {
    const auto &data = *static_cast<const TwistZData *>(block);
    const auto a = bodyA * data.frameA, b = bodyB * data.frameB;
    offset = PxVec3(0); pointA = a.p; pointB = b.p;
    massScale = PxConstraintInvMassScale(1, 1, 1, 1);
    if (maxRows < (data.limit == 0 ? 1u : 2u)) return 0;
    PxQuat q = (a.q.getConjugate() * b.q).getNormalized();
    if (data.limit == 0) {
        // q.z=0 describes zero local-Z twist for arbitrary free swing, even
        // at 180-degree swing where atan2(z,w) itself is undefined. Use the
        // signed geodesic distance to this equality and its unit gradient.
        // This is a hard constraint row, not an extra spring or pose clamp.
        if (q.w < 0) q = -q;
        PxVec3 gradient(q.y, -q.x, q.w);
        const float n = gradient.magnitude();
        gradient = n > 1e-7f ? gradient / n : PxVec3(0, 0, 1);
        auto &row = rows[0];
        row = Px1DConstraint{};
        row.angular0 = row.angular1 = a.q.rotate(gradient);
        row.geometricError = -2 * std::atan2(q.z, n);
        row.minImpulse = -PX_MAX_F32;
        row.maxImpulse = PX_MAX_F32;
        row.flags = Px1DConstraintFlag::eANGULAR_CONSTRAINT | Px1DConstraintFlag::eOUTPUT_FORCE;
        row.solveHint = PxConstraintSolveHint::eEQUALITY;
        return 1;
    }
    const float den = q.w * q.w + q.z * q.z;
    // At exactly 180-degree swing this coordinate is undefined. Do not
    // invent an extra swing constraint to avoid that coordinate singularity.
    if (den < 1e-12f) return 0;
    const float root = std::sqrt(den);
    // d(theta)/d(omega_B-omega_A), in A's frame:
    // [(w*y+z*x)/den, (z*y-w*x)/den, 1]. Its norm is 1/sqrt(den).
    // Normalize BOTH Jacobian and positional error by that norm. This keeps
    // the solver row finite even near 180-degree swing without changing DOFs.
    const PxVec3 axis = a.q.rotate(PxVec3((q.w*q.y+q.z*q.x)/root,
                                         (q.z*q.y-q.w*q.x)/root, root));
    const float angle = principalTwistZ(q);
    for (int side = 0; side < 2; ++side) {
        auto &row = rows[side];
        row = Px1DConstraint{};
        const float sign = side == 0 ? -1.f : 1.f;
        row.angular0 = row.angular1 = axis * sign;
        row.geometricError = (data.limit - sign * angle) * root;
        row.minImpulse = 0;
        row.maxImpulse = PX_MAX_F32;
        row.flags = Px1DConstraintFlag::eANGULAR_CONSTRAINT | Px1DConstraintFlag::eOUTPUT_FORCE;
        // Inside the interval, retain the positive distance-to-bound bias in
        // velocity iterations; otherwise both inactive limits brake ALL twist.
        if (row.geometricError > 0) row.flags |= Px1DConstraintFlag::eKEEPBIAS;
        row.solveHint = PxConstraintSolveHint::eINEQUALITY;
    }
    return 2;
}

class TwistZConstraint : public PxConstraintConnector {
    TwistZData data;
    PxRigidDynamic *a, *b;
    PxTransform localA, localB;
    PxConstraint *constraint = nullptr;
public:
    TwistZConstraint(PxRigidDynamic *actorA, PxRigidDynamic *actorB,
                     const PxTransform &frameA, const PxTransform &frameB, float limit)
        : a(actorA), b(actorB), localA(frameA), localB(frameB) {
        data.limit = limit;
        onComShift(0); onComShift(1);
    }
    static TwistZConstraint *create(PxPhysics &physics, PxRigidDynamic *a, PxRigidDynamic *b,
                                    const PxTransform &fa, const PxTransform &fb, float limit, bool noPrep) {
        auto *owner = new TwistZConstraint(a, b, fa, fb, limit);
        const PxConstraintShaderTable shader{twistZPrep, nullptr, PxConstraintFlag::eCOLLISION_ENABLED};
        owner->constraint = physics.createConstraint(a, b, *owner, shader, sizeof(TwistZData));
        require(owner->constraint != nullptr, "twist_z custom constraint creation failed");
        owner->constraint->setFlag(PxConstraintFlag::eDISABLE_PREPROCESSING, noPrep);
        return owner;
    }
    float angle() const {
        const PxQuat qa = a->getGlobalPose().q * localA.q;
        const PxQuat qb = b->getGlobalPose().q * localB.q;
        return principalTwistZ(qa.getConjugate() * qb);
    }
    float lockError() const {
        const PxQuat qa = a->getGlobalPose().q * localA.q;
        const PxQuat qb = b->getGlobalPose().q * localB.q;
        const PxQuat q = (qa.getConjugate() * qb).getNormalized();
        return 2 * std::asin(std::clamp(std::abs(q.z), 0.f, 1.f));
    }
    void *prepareData() override { return &data; }
    bool updatePvdProperties(physx::pvdsdk::PvdDataStream&, const PxConstraint*, PxPvdUpdateType::Enum) const override { return false; }
    void updateOmniPvdProperties() const override {}
    void onConstraintRelease() override { delete this; }
    void onComShift(PxU32 actor) override {
        if (actor == 0) data.frameA = a->getCMassLocalPose().transformInv(localA);
        else data.frameB = b->getCMassLocalPose().transformInv(localB);
        if (constraint) constraint->markDirty();
    }
    void onOriginShift(const PxVec3&) override {}
    void *getExternalReference(PxU32 &type) override { type = 0x54575A; return this; }
    PxBase *getSerializable() override { return nullptr; }
    PxConstraintSolverPrep getPrep() const override { return twistZPrep; }
    const void *getConstantBlock() const override { return &data; }
};
