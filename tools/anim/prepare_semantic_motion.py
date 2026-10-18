"""Stage gentle-v1 from verified, unsimulated release inputs before physics.

Keep the accepted retarget/root corrections and facial performance. Graft the
changed shared spine/camera channels into every dependent head source. Inputs
and the active release are read-only; outputs are explicitly isolated candidates.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'tools'/d) for d in ('lib', 'anim', 'physics')]
import moe_release_sources as RELEASE
import moe_anim_catalog as CAT
import moe_semantic_motion as S
import moe_chars as CHARS
import moe_bullet_bake as B
import pdx_anim_write as W
import pdx_parse as P


def read_clip(path):
    meta, bones, samples = B.RV.load_anim(path)
    frames, ts, qs = B.RV.unpack_samples(meta, bones, samples)
    return dict(frames=frames, fps=float(meta['fps'][0]), bones=bones,
                rest={b['name']: b for b in bones}, ts=ts, qs=qs)


def write_clip(path, clip):
    bones = [(b['name'], ('t' if b['name'] in clip['ts'] else '') +
              ('q' if b['name'] in clip['qs'] else ''), b['t'], b['q'], b['s']) for b in clip['bones']]
    samples = {c: [v for f in range(clip['frames']) for n, flags, *_ in bones if c in flags
                   for v in clip[c+'s'][n][f]] for c in ('t', 'q')}
    W.write_anim(path, clip['fps'], clip['frames'], bones, samples['t'], samples['q'])


def world(clip, parents):
    return B.fk_frames(clip['frames'], clip['rest'], clip['ts'], clip['qs'], parents,
                       B.topo_order(list(clip['rest']), parents))


def channel(clip, bone, kind):
    return np.asarray(clip[kind+'s'].get(bone, [clip['rest'][bone][kind]]*clip['frames']))


def prepare(character, destination):
    selected = RELEASE.resolve()
    if selected is None:
        raise ValueError('An explicit verified source release is required')
    role = RELEASE.role(character, selected)
    destination = Path(destination).resolve()
    if not destination.is_relative_to(ROOT/'work/evidence'):
        raise ValueError('Candidate output must be inside work/evidence/')
    if destination.exists():
        raise FileExistsError('Use a new candidate directory: '+str(destination))
    source = role['base']
    canonical = role['canonical']
    manifest_path = RELEASE.reference(role['provenance']['base_manifest'])
    source_manifest = RELEASE.read(manifest_path)
    assert source_manifest['status'] == 'complete' and source_manifest['source_kind'] == 'canonical_base'
    root_contract = RELEASE.reference(role['provenance']['source_root_contract'])
    files = source_manifest['files']
    assert {r['filename'] for r in files} == {p.name for p in source.glob('*.anim')}
    destination.mkdir(parents=True)
    out = destination/'anims'
    out.mkdir()
    expected = {}
    for row in files:
        p = source/row['filename']
        assert RELEASE.sha(p) == row['sha256'], p
        shutil.copyfile(p, out/p.name)
        expected[p.name] = row['sha256']
    mesh = canonical/'moe_torso.mesh'
    parents = B.RV.mesh_parents(mesh)
    axes = {}
    for node_path, key, _kind, values in P.walk(P.parse(mesh.read_bytes())):
        if '/skeleton/' in node_path and key == 'tx':
            inv = np.asarray(values).reshape(4, 3).T[:, :3]
            axes[node_path.rsplit('/', 1)[-1]] = S.axes_from_inverse_bind(inv)
    prefix = CHARS.BY_CK[character]['prefix']
    catalog = CAT.Catalog('torso')
    pairs = CAT.pair_head_to_torso(catalog.bases)
    contact = CAT.hand_prop_nodes()
    changed = {}
    decisions = {}
    camera = 'camera_torso_look_at'
    shared = (*S.SPINES, camera)
    for action in S.PROFILES:
        filename = prefix+'body_'+action+'.anim'
        if filename not in expected:
            raise ValueError('Missing reviewed body source '+filename)
        base = read_clip(source/filename)
        c = copy.deepcopy(base)
        uses = S.loop_uses('body_'+action, catalog.states, CAT.fold)
        c['qs'], report = S.apply(action, c['frames'], c['fps'], c['rest'], c['qs'], axes,
            looping=bool(uses['loop_states']), contact_sensitive=bool(contact.get('body_'+action)))
        report['state_uses'] = uses
        decisions[action] = report
        if not report['applied']:
            continue
        original_world = world(base, parents)
        revised_world = world(c, parents)
        pr, pp = revised_world[parents[camera]]
        wr, wp = original_world[camera]
        inv = pr.transpose(0, 2, 1)
        c['ts'][camera] = np.einsum('fij,fj->fi', inv, wp-pp).tolist()
        c['qs'][camera] = B.ms_to_qs(inv@wr).tolist()
        write_clip(out/filename, c)
        loaded = read_clip(out/filename)
        assert loaded['fps'] == base['fps'] and loaded['frames'] == base['frames']
        assert list(loaded['rest']) == list(base['rest'])
        # Every non-spine/camera channel remains exactly equal after float32 I/O.
        for bone in base['rest']:
            if bone in shared:
                continue
            for kind in ('t','q'):
                assert np.array_equal(channel(base,bone,kind), channel(loaded,bone,kind)), (bone,kind)
        current_world = world(loaded, parents)
        report['feet_delta_max_model_units'] = max(float(np.linalg.norm(original_world[n][1]-current_world[n][1],axis=1).max())
            for n in ('bn_m_l_ankle','bn_m_r_ankle'))
        report['camera_delta_max_model_units'] = float(np.linalg.norm(original_world[camera][1]-current_world[camera][1],axis=1).max())
        report['spine_angle_max_deg'] = {}
        for n in S.SPINES:
            old, new = S.normalize(channel(base,n,'q')), S.normalize(channel(loaded,n,'q'))
            angles = np.degrees(2*np.arccos(np.clip(np.abs(np.sum(old*new,axis=1)),0,1)))
            report['spine_angle_max_deg'][n] = float(angles.max())
            assert angles[[0,-1]].max() < 1e-4
        assert report['feet_delta_max_model_units'] < 1e-5
        assert report['camera_delta_max_model_units'] < 1e-4
        changed['body_'+action] = loaded
        print(character, action, 'spine', report['spine_angle_max_deg'], flush=True)
    grafted = []
    for p in sorted(source.glob(prefix+'head_*.anim')):
        head_action = p.stem[len(prefix):]
        body_action = pairs.get(head_action, 'body_idle')
        if body_action not in changed:
            continue
        body = changed[body_action]
        c = read_clip(p)
        assert c['frames'] == body['frames'] and c['fps'] == body['fps'], head_action
        for n in shared:
            assert n in c['rest']
            for kind in ('t','q'):
                if n in body[kind+'s']:
                    c[kind+'s'][n] = copy.deepcopy(body[kind+'s'][n])
                else:
                    c[kind+'s'].pop(n, None)
                c['rest'][n][kind] = list(body['rest'][n][kind])
        write_clip(out/p.name, c)
        check = read_clip(out/p.name)
        old = read_clip(p)
        for n in c['rest']:
            for kind in ('t','q'):
                target = body if n in shared else old
                assert np.array_equal(channel(check,n,kind),channel(target,n,kind)),(head_action,n,kind)
        grafted.append(dict(head=head_action,body=body_action))
    rows = []
    for p in sorted(out.glob('*.anim')):
        digest = RELEASE.sha(p)
        rows.append(dict(filename=p.name, source_sha256=expected[p.name], sha256=digest,
                         changed=digest != expected[p.name]))
    permitted = {prefix+b+'.anim' for b in changed} | {prefix+r['head']+'.anim' for r in grafted}
    assert all(not row['changed'] or row['filename'] in permitted for row in rows)
    report = dict(schema=1,status='verified_prephysics_candidate_not_physics_acceptance',character=character,
        upstream_release=dict(path=str(selected['path']),sha256=selected['sha256']),
        source_manifest=dict(path=str(manifest_path),sha256=RELEASE.sha(manifest_path)),
        source_root_contract=dict(path=str(root_contract),sha256=RELEASE.sha(root_contract)),
        source_dir=str(source), output_dir=str(out), semantic_motion=S.VERSION,
        implementation={str(Path(__file__).relative_to(ROOT)):RELEASE.sha(__file__),
                        str(Path(S.__file__).relative_to(ROOT)):RELEASE.sha(S.__file__)},
        meshes={part:dict(path=str(canonical/('moe_'+part+'.mesh')),
            sha256=RELEASE.sha(canonical/('moe_'+part+'.mesh'))) for part in ('head','torso')},
        decisions=decisions, grafted_heads=grafted, files=rows,
        unchanged_files=sum(not r['changed'] for r in rows), changed_files=sum(r['changed'] for r in rows),
        guarantees=['source bytes verified against immutable base manifest',
                    'all non-spine/camera body channels exactly preserved including root and legs',
                    'every dependent head shares exact spine/camera samples; facial channels preserved',
                    'fps/frame count and endpoint added pose preserved',
                    'no already simulated animation used as an input'])
    (destination/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(character, 'COMPLETE', report['changed_files'], 'changed;',report['unchanged_files'],'byte-identical',flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--character',required=True,choices=CHARS.BY_CK)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args()
    prepare(a.character,a.out_dir)


if __name__ == '__main__':
    main()
