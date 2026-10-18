"""Skeleton carriers for the moe portrait entities (CK3 1.20).

Why this exists
---------------
CK3 1.20 caches portrait bones by **index**: a camera node, a shared pose and the
cross-entity pose lookups resolve a bone name once and reuse the index on other
skeletons. Our real meshes have 192~428 bones in their own order, the vanilla body
has 134, so every lookup that crossed skeletons read a wrong bone (camera framed
the skirt, cha's legs) or ran past the array (EXCEPTION_ACCESS_VIOLATION at
ck3.exe+0x3d11b62 with bone index 182/184/241).

Layout produced here (per character <ck>):
  * torso carrier  models/<pre>torso_carrier.mesh
      full torso skeleton (same order as the real torso), one degenerate triangle.
      Holds every entity state; plays the merged head+torso anims (merge_portrait_anims.py).
  * head carrier   models/moe_cam_rig.mesh (shared by every character)
      3 private bones: moe_cam_root / moe_cam_head / moe_cam_torso. Nothing else.
      Attached to the torso carrier's ground_joint, so it never follows body motion;
      the private moe_camera_* cameras look through it.
  * head carrier static anim  models/<pre>cam_rig.anim
      puts moe_cam_torso / moe_cam_head at this character's own camera height.
  * real head / real torso
      accessories with shared_pose_entity = torso, re-skinned onto the torso carrier's
      bone table (pose is copied by index, so the tables must be identical).

usage: python tools/build/build_portrait_carriers.py <pack dir> [<pack dir> ...]
"""
import re
import struct
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / 'lib')]
import numpy as np
import pdx_parse as P
from pdx_anim_write import write_anim

ASSET_REL = Path('gfx/models/portraits/moe')
SK_TAG = b'[[[skeleton\x00'
MESH_TAG = b'[[[mesh\x00'
RIG_MESH = 'moe_cam_rig.mesh'
RIG_BONES = ('moe_cam_root', 'moe_cam_head', 'moe_cam_torso')


# ---------------------------------------------------------------- binary writers
def _name(n):
    b = n.encode()
    return bytes([len(b)]) + b


def prop_f(n, v):
    return b'!' + _name(n) + b'f' + struct.pack('<I', len(v)) + struct.pack('<%df' % len(v), *v)


def prop_i(n, v):
    return b'!' + _name(n) + b'i' + struct.pack('<I', len(v)) + struct.pack('<%di' % len(v), *v)


def prop_s(n, s):
    b = s.encode() + b'\x00'
    return b'!' + _name(n) + b's' + struct.pack('<I', 1) + struct.pack('<I', len(b)) + b


def node(n, depth):
    return b'[' * depth + n.encode() + b'\x00'


def carrier_submesh(material_shader='portrait_attachment_alpha_to_coverage',
                    diffuse='moe_carrier_clear.dds'):
    """One zero-area triangle at the origin, skinned 100% to bone 0."""
    eps = 0.008
    p = [0.0, 0.0, 0.0, eps, 0.0, 0.0, 0.0, eps, 0.0]
    n = [0.0, 0.0, 1.0] * 3
    ta = [1.0, 0.0, 0.0, 1.0] * 3
    uv = [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    out = node('mesh', 3)
    out += prop_f('p', p) + prop_f('n', n) + prop_f('ta', ta) + prop_f('u0', uv)
    out += prop_i('tri', [0, 1, 2]) + prop_f('boundingsphere', [0.0, 0.0, 0.0, 0.01])
    out += node('aabb', 4) + prop_f('min', [0.0, 0.0, 0.0]) + prop_f('max', [eps, eps, 0.0])
    out += node('material', 4) + prop_s('shader', material_shader) + prop_s('diff', diffuse)
    out += prop_s('n', diffuse) + prop_s('spec', diffuse)
    out += node('skin', 4) + prop_i('bones', [4])
    out += prop_i('ix', [0, -1, -1, -1] * 3) + prop_f('w', [1.0, 0.0, 0.0, 0.0] * 3)
    return out


def skeleton_section(bones):
    """bones: [(name, parent_index or None, world 4x4)] -> skeleton node bytes (inverse bind)."""
    out = node('skeleton', 3)
    for i, (name, pa, W) in enumerate(bones):
        inv = np.linalg.inv(W)
        R = inv[:3, :3]
        tx = list(R.T.reshape(-1)) + list(inv[:3, 3])
        out += node(name, 4) + prop_i('ix', [i])
        if pa is not None:
            out += prop_i('pa', [pa])
        out += prop_f('tx', [float(x) for x in tx])
    return out


def write_mesh(path, obj_name, submesh, skeleton):
    out = b'@@b@' + prop_i('pdxasset', [1, 0]) + node('object', 1) + node(obj_name, 2)
    out += submesh + skeleton + node('locator', 1)
    Path(path).write_bytes(out)


# ---------------------------------------------------------------- readers
def split_skeleton(data):
    a, b = data.find(MESH_TAG), data.find(SK_TAG)
    assert 0 < a < b
    return data[:a], data[a:b], data[b:]


def bind(mesh):
    t = P.parse(Path(mesh).read_bytes()); out = []
    def find(n):
        for cn, ch in n.get('_children', []):
            if cn == 'skeleton':
                for bn, b in ch['_children']:
                    tx = b['_props']['tx'][1]; inv = np.eye(4)
                    inv[:3, :3] = np.array(tx[:9]).reshape(3, 3).T; inv[:3, 3] = tx[9:12]
                    pa = b['_props'].get('pa')
                    out.append((bn, pa[1][0] if pa else None, np.linalg.inv(inv)))
            find(ch)
    find(t)
    return out


def m2q(R):
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1) * 2
        q = [(R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s]
    else:
        i = int(np.argmax(np.diag(R)))
        if i == 0:
            s = np.sqrt(1 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
            q = [0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s, (R[2, 1] - R[1, 2]) / s]
        elif i == 1:
            s = np.sqrt(1 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
            q = [(R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s, (R[0, 2] - R[2, 0]) / s]
        else:
            s = np.sqrt(1 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
            q = [(R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s, (R[1, 0] - R[0, 1]) / s]
    q = np.array(q)
    return [float(x) for x in q / np.linalg.norm(q)]


# ---------------------------------------------------------------- products
def torso_carrier(real_torso, out):
    """Same skeleton bytes as the real torso; geometry replaced by one triangle."""
    d = Path(real_torso).read_bytes()
    pre, _mesh, sk = split_skeleton(d)
    Path(out).write_bytes(pre + carrier_submesh() + sk)


def rig_mesh(out):
    """Shared head carrier: three private bones at the origin, identity orientation."""
    I = np.eye(4)
    bones = [(RIG_BONES[0], None, I), (RIG_BONES[1], 0, I), (RIG_BONES[2], 0, I)]
    write_mesh(out, 'moe_cam_rigShape', carrier_submesh(), skeleton_section(bones))


def rig_anim(torso_carrier_mesh, out, attach='ground_joint', fps=30.0, frames=2):
    """Static pose that puts the rig bones at this character's camera height.

    The rig root is joint-attached to <attach> on the torso carrier. Root local = that bone's
    bind transform so the root lands in world identity; children are expressed in root space.
    Head target is lifted a little above camera_torso_look_at so look-at framing keeps the face.
    """
    B = {n: W for n, _pa, W in bind(torso_carrier_mesh)}
    Wa = B[attach]
    inv = np.linalg.inv(Wa)
    cam = B['camera_torso_look_at'][:3, 3]
    head = B.get('bn_h_head_mid', B['camera_torso_look_at'])[:3, 3] + np.array([0.0, 2.5, 0.0])
    q_world = m2q(inv[:3, :3])
    root_t = [float(x) for x in Wa[:3, 3]]
    root_q = m2q(Wa[:3, :3])
    loc = lambda p: [float(x) for x in (inv @ np.append(p, 1.0))[:3]]
    bones = [(RIG_BONES[0], '', root_t, root_q, [1.0]),
             (RIG_BONES[1], '', loc(head), q_world, [1.0]),
             (RIG_BONES[2], '', loc(cam), q_world, [1.0])]
    write_anim(out, fps, frames, bones, [], [], drop_constant=False)
    return cam, head


def reskin(real_mesh, carrier_mesh, out):
    """Give real_mesh the carrier's bone table; remap skin indices by bone name."""
    src = Path(real_mesh).read_bytes()
    ref = Path(carrier_mesh).read_bytes()
    pre, _m, sk = split_skeleton(src)
    _rpre, _rm, rsk = split_skeleton(ref)
    names = [n for n, _p, _w in bind(real_mesh)]
    rnames = [n for n, _p, _w in bind(carrier_mesh)]
    ridx = {n: i for i, n in enumerate(rnames)}
    missing = [n for n in names if n not in ridx]
    if missing:
        raise SystemExit(f'{real_mesh}: bones missing on carrier: {missing[:5]}')
    remap = [ridx[n] for n in names]
    body = bytearray(src[:src.find(SK_TAG)])
    pos = 0
    while True:
        i = body.find(b'[[[[skin\x00', pos)
        if i < 0:
            break
        k = body.find(b'!\x02ixi', i)
        cnt = struct.unpack_from('<I', body, k + 5)[0]
        base = k + 9
        vals = struct.unpack_from('<%di' % cnt, body, base)
        struct.pack_into('<%di' % cnt, body, base, *[remap[v] if v >= 0 else v for v in vals])
        pos = base + 4 * cnt
    # skeleton + trailing locator from the carrier
    Path(out).write_bytes(bytes(body) + rsk)


def _job(args):
    kind = args[0]
    if kind == 'torso':
        torso_carrier(args[1], args[2])
    elif kind == 'reskin':
        reskin(args[1], args[2], args[3])
    elif kind == 'anim':
        rig_anim(args[1], args[2])
    return args


def main():
    for pack in sys.argv[1:]:
        moe = Path(pack) / ASSET_REL
        models = moe / 'models'
        reals = sorted(models.glob('moe_*torso.mesh'))
        print(pack, len(reals), 'characters')
        rig_mesh(models / RIG_MESH)
        with ProcessPoolExecutor(16) as ex:
            list(ex.map(_job, [('torso', r, r.with_name(r.stem + '_carrier.mesh')) for r in reals]))
            jobs = []
            for r in reals:
                pre = r.name[:-len('torso.mesh')]
                c = r.with_name(r.stem + '_carrier.mesh')
                jobs.append(('anim', c, models / f'{pre}cam_rig.anim'))
                jobs.append(('reskin', r, c, r))
                h = models / f'{pre}head.mesh'
                if h.exists():
                    jobs.append(('reskin', h, c, h))
            list(ex.map(_job, jobs))


if __name__ == '__main__':
    main()
