"""Isolated, resumable ten-action response trial across registered characters.

Explicit inventory and output root are mandatory. Never writes release assets.
Each character is a separate process because the legacy writer has global state.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import struct
import time
import traceback
from types import MethodType

os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools'/p) for p in ('physics','lib','anim')]
import numpy as np
import moe_bullet_bake as B
import explicit_bake_inputs as E
import physics_response as R
import moe_release_sources as S
import moe_anim_observability as O
import moe_bone_contract as C
import trial_artifact_cache as AC

ACTIONS=('idle','writing','sitting','dance_loop','dance_spin','emotion_laugh','emotion_shock','emotion_fear','emotion_angry_start','jockey_lance_couched_gallop')
NONLOOP={'dance_spin','emotion_angry_start'}


def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf8');t.replace(p)


def trial_actions(root,ck=None):
    scope=Path(root)/'trial_scope.json'
    actions=read(scope).get('actions',list(ACTIONS)) if scope.exists() else list(ACTIONS)
    # 'all' = every action listed in that role's inventory (full set, one native
    # batch per role); roles differ slightly (345/346), so it is per character.
    if actions=='all':
        if ck is None:raise ValueError('Full action scope is per character')
        return tuple(sorted(read(Path(root)/f'{ck}_inventory.json')['source_files']))
    if (not actions or len(actions)!=len(set(actions)) or
        not set(actions)<=set(ACTIONS)):
        raise ValueError(f'Invalid trial action scope: {actions!r}')
    return tuple(actions)


def is_loop(ck,action):
    # The ten fixed actions keep their historical flag; the rest use the
    # engine state table (full animation id, see moe_bullet_bake.is_looping).
    if action in ACTIONS:return action not in NONLOOP
    return B.is_looping(B.X.BY_CK[ck]['prefix']+'body_'+action)


def stage(root,ck):
    actions=trial_actions(root,ck)
    inv=read(root/f'{ck}_inventory.json');folder=root/'characters'/ck;folder.mkdir(parents=True,exist_ok=True)
    source=folder/'sources';source.mkdir(exist_ok=True)
    meshes=dict(inv['mesh']);release=S.role(ck)
    # Semantic sources for the old 31 use the reviewed immutable mesh binding.
    if release:
        meshes={part:str(release['canonical']/f'moe_{part}.mesh') for part in ('head','torso')}
    # Candidate-only mesh swap (e.g. re-weighted head from an expression rebake); the
    # bone-table check below still requires every staged clip to match it exactly.
    override=(read(root/'experiment.json').get('mesh_overrides_by_character') or {}).get(ck)
    if override:
        meshes.update({part:str(p) for part,p in override.items()})
    # Head animations matching an overridden head mesh (same leaf names, so the
    # head graft hash inputs stay stable); body sources always come from inventory.
    head_dir=(read(root/'experiment.json').get('head_sources_by_character') or {}).get(ck)
    hashes={}
    for action in actions:
        for part in ('head','body'):
            p=Path(inv['source_files'][action][part])
            if part=='head' and head_dir:p=Path(head_dir)/p.name
            dest=source/(B.X.BY_CK[ck]['prefix']+part+'_'+action+'.anim')
            digest=sha(p)
            if not dest.exists():shutil.copy2(p,dest)
            if sha(dest)!=digest:raise ValueError(f'Changed staged input {dest}')
            hashes[str(dest)]=dict(origin=str(p),sha256=digest)
    for part,p in meshes.items():hashes[str(p)]=dict(role=part,sha256=sha(p))
    hashes[inv['pmx']]=dict(role='pmx',sha256=sha(inv['pmx']))
    # Verify exact animation-to-destination bone tables before preparing physics.
    for part,mkey in [('head','head'),('body','torso')]:
        expected=list(O.from_mesh(meshes[mkey],protected_names=()).bone_names)
        for action in actions:
            p=source/(B.X.BY_CK[ck]['prefix']+part+'_'+action+'.anim')
            meta,bones,_=B.RV.load_anim(p)
            names=[b['name'] for b in bones]
            if names!=expected:raise ValueError(f'Bone table mismatch: {ck}/{part}/{action}, {len(names)} != {len(expected)}')
    config=dict(character=ck,version=R.VERSION,actions=list(actions),meshes=meshes,source_dir=str(source),pmx=inv['pmx'],
                source_hashes=hashes,native_sha256=sha(root/'native/moe_physx_response.exe'),
                response_py_sha256=sha(Path(R.__file__)),runner_sha256=sha(Path(__file__)),
                code_fingerprint=code_fingerprint(),inventory_sha256=sha(root/f'{ck}_inventory.json'),
                options_template_sha256=sha(root/'options_template.json'),accepted=False,installed=False)
    config['experiment']=read(root/'experiment.json') if (root/'experiment.json').exists() else {}
    previous=folder/'configuration.json'
    if previous.exists():
        old=read(previous)
        for field in ('source_hashes','native_sha256','response_py_sha256','meshes','experiment'):
            if old.get(field,{})!=config[field]:raise ValueError(f'Resume contract changed: {ck}/{field}; use a fresh output root')
    save(previous,config)
    return config,folder


def options(root,folder,ck):
    base=read(root/'options_template.json')
    base.update(ck=ck,exe=str(root/'native/moe_physx_response.exe'),io_dir=str(folder/'io'),out_dir=str(folder/'anims'),
                stats_json=str(folder/'solver_stats.json'),batch_size=len(trial_actions(root,ck)),release_recipe=False,ang_damp_cap=1.,
                profile=None,ball_cap=None,wind=0.,physx_bone_twist_projection=True,
                physx_angular_limit_hz=0,physx_angular_limit_rigids=None,
                physx_contact_frequency_hz=0.,physx_contact_rigids=None,physx_iters=8,physx_velocity_iters=1)
    if (root/'experiment.json').exists():
        experiment=read(root/'experiment.json');base.update(experiment.get('solver',{}))
        # 按角色覆盖求解/写回选项（2026-10-12 dual_side_writeback），缺省不加任何键 = 旧选项不变
        base.update((experiment.get('solver_by_character') or {}).get(ck,{}))
    return argparse.Namespace(**base)


# Runner revisions whose baked roles stay valid: they differ from this one only
# in how the solve is scheduled (--gpu-chunks), not in what is written.
# Runner revisions whose changes do not touch solver inputs or outputs (scheduling only),
# so their cached roles stay valid: 5a1d... = before --gpu-devices.
COMPATIBLE_RUNNERS={'2c15d8bec4a1675c740058293cd86810d91d752fb0f6c81dd3223262e45b2777',
                    '5a1d20486716c0f3437d3c021dece4256b3c0300facb8057aea955e32ad2172b'}


def code_fingerprint():
    modules={
        'runner':Path(__file__), 'bake':Path(B.__file__), 'inputs':Path(E.__file__),
        'response':Path(R.__file__), 'release_sources':Path(S.__file__),
        'observability':Path(O.__file__), 'bone_contract':Path(C.__file__),
        'artifact_cache':Path(AC.__file__),
    }
    return {name:sha(path) for name,path in modules.items()}


def cached_result_is_valid(root,ck,actions):
    """Cheaply prove a baked character still matches its immutable trial inputs."""
    root=Path(root);folder=root/'characters'/ck
    try:
        cfg=read(folder/'configuration.json');result=read(folder/'results.json')
        inv=read(root/f'{ck}_inventory.json');experiment=read(root/'experiment.json')
        executable=sha(root/'native/moe_physx_response.exe')
        if (result.get('status')!='baked' or result.get('installed') or result.get('accepted') or
                cfg.get('character')!=ck or result.get('character')!=ck or
                cfg.get('actions')!=list(actions) or cfg.get('experiment')!=experiment or
                cfg.get('native_sha256')!=executable or
                cfg.get('response_py_sha256')!=sha(Path(R.__file__))):
            return None
        if cfg.get('code_fingerprint') is not None:
            stored=dict(cfg['code_fingerprint'])
            if stored.get('runner') in COMPATIBLE_RUNNERS:stored['runner']=sha(Path(__file__))
            if stored!=code_fingerprint():return None
        if cfg.get('inventory_sha256') is not None and cfg['inventory_sha256']!=sha(root/f'{ck}_inventory.json'):return None
        if cfg.get('options_template_sha256') is not None and cfg['options_template_sha256']!=sha(root/'options_template.json'):return None
        effective=read(folder/'effective_options.json')
        if effective!=vars(options(root,folder,ck)):return None
        expected_names={B.X.BY_CK[ck]['prefix']+part+'_'+action+'.anim'
                        for action in actions for part in ('head','body')}
        if len(result.get('output_hashes',{}))!=len(expected_names):return None
        output_names={Path(path).name for path in result['output_hashes']}
        if output_names!=expected_names:return None
        # Verify all source/mesh/PMX inputs and the exact prepared solver inputs.
        for path,record in cfg.get('source_hashes',{}).items():
            if sha(path)!=record['sha256']:return None
            origin=record.get('origin')
            if origin and sha(origin)!=record['sha256']:return None
        for path,digest in result['output_hashes'].items():
            if sha(path)!=digest:return None
        prepared=read(folder/'prepared.json')
        if prepared.get('status')!='prepared':return None
        expected_prepared_names={B.X.BY_CK[ck]['prefix']+'body_'+action+suffix
                                 for action in actions for suffix in ('.scene.txt','.frames.bin')}
        if {Path(path).name for path in prepared.get('files',{})}!=expected_prepared_names:return None
        for path,digest in prepared.get('files',{}).items():
            if sha(path)!=digest:return None
        contract_path=folder/'response_contract.json'
        read(contract_path)
        if result.get('response_contract_sha256') and sha(contract_path)!=result['response_contract_sha256']:return None
        rigids=len(inv['rigids'])
        batch_record_path=folder/'io/batch_command.json'
        batch_record=read(batch_record_path) if batch_record_path.exists() else None
        batch_command=batch_record.get('command') if batch_record else None
        if batch_record and (batch_record.get('solver_sha256')!=executable or
                             batch_record.get('input_hashes')!=prepared['files']):return None
        # Chunked roles record one command per chunk plus the action list of each.
        chunk_of={};chunk_cmds=None
        if batch_record and batch_record.get('chunks'):
            chunk_cmds=batch_command if batch_command and isinstance(batch_command[0],list) else [batch_command]
            if len(chunk_cmds)!=len(batch_record['chunks']):return None
            for ci,names in enumerate(batch_record['chunks']):
                for name in names:chunk_of[name]=ci
        common_command=None
        for action in actions:
            base=B.X.BY_CK[ck]['prefix']+'body_'+action
            fp=folder/'io'/(base+'.frames.bin');op=folder/'io'/(base+'.out.bin')
            command=read(str(op)+'.command.json');report=read(str(op)+'.solver.json')
            if chunk_cmds is not None:
                if base not in chunk_of:return None
                ci=chunk_of[base];expected=chunk_cmds[ci];instances=len(batch_record['chunks'][ci])
                if common_command is None:common_command=expected
            else:
                if common_command is None:common_command=command.get('command')
                if command.get('command')!=common_command:return None
                expected=batch_command or common_command;instances=len(actions)
            with fp.open('rb') as stream:source_frames=struct.unpack('<ii',stream.read(8))[0]
            tail=min(24,source_frames//2) if is_loop(ck,action) and source_frames>=4 else 0
            error=AC.raw_output_error(output=op,frames=fp,rigids=rigids,tail_frames=tail,
                report=report,command_record=command,solver_sha256=executable,
                expected_command=expected,batch_instances=instances)
            if error:return None
            expected_options=vars(options(root,folder,ck))
            if (report.get('actual_hz')!=expected_options.get('hz') or
                    report.get('position_iterations')!=expected_options.get('physx_iters') or
                    report.get('velocity_iterations')!=expected_options.get('physx_velocity_iters') or
                    report.get('bodies')!=rigids or report.get('solver')!='TGS' or
                    report.get('backend')!='physx-gpu'):
                return None
        if not common_command or str(Path(common_command[0]).resolve())!=str((root/'native/moe_physx_response.exe').resolve()):return None
        if chunk_cmds is None and batch_record and batch_record.get('command')!=common_command:return None
        if result.get('artifact_fingerprint'):
            fingerprint=AC.canonical_sha256(dict(configuration=cfg,effective_options=effective,
                prepared=prepared['files'],contract_sha256=sha(contract_path),
                outputs=result['output_hashes'],batch_command=batch_command if chunk_cmds is not None else common_command))
            if fingerprint!=result['artifact_fingerprint']:return None
        return result
    except (OSError,KeyError,TypeError,ValueError,struct.error):
        return None


class stage_slot:
    """Cross-process stage gate: at most N workers inside one pipeline stage.

    Three stages per role: load (stage + prepare, CPU), gpu (native solve, VRAM),
    write (write-back + checks, CPU). --load-jobs / --gpu-jobs / --write-jobs cap
    each stage; --jobs is the pool size = roles in flight (commit memory bound).

    Preparation and write-back are CPU-bound and run on every worker; only the
    native batch holds VRAM (~4 GB for a 331-action batch on a 12 GB card), so
    --jobs sizes the CPU pool and --gpu-jobs caps concurrent solves. Slots are
    exclusive-create lock files under the root; a crashed holder's slot is
    freed when its PID is gone.
    """
    def __init__(self,root,n,name='gpu'):self.dir=Path(root)/f'{name}_slots';self.n=n;self.path=None;self.index=0
    def __enter__(self):
        if not self.n:return self
        self.dir.mkdir(exist_ok=True)
        while True:
            for i in range(self.n):
                p=self.dir/f'slot{i}'
                try:
                    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
                    self.path=p;self.index=i;return self
                except FileExistsError:
                    try:pid=int(p.read_text() or 0)
                    except (OSError,ValueError):continue
                    if pid and not _pid_alive(pid):
                        try:p.unlink()
                        except OSError:pass
            time.sleep(2)
    def __exit__(self,*_):
        if self.path:
            try:self.path.unlink()
            except OSError:pass
            self.path=None


def _pid_alive(pid):
    import ctypes
    h=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)
    if not h:return False
    code=ctypes.c_ulong();ctypes.windll.kernel32.GetExitCodeProcess(h,ctypes.byref(code));ctypes.windll.kernel32.CloseHandle(h)
    return code.value==259


def worker(root,ck,gpu_jobs=0,load_jobs=0,write_jobs=0,gpu_chunks=1,gpu_devices=0):
    root=Path(root);actions=trial_actions(root,ck);folder=root/'characters'/ck;folder.mkdir(parents=True,exist_ok=True)
    # Full logs stay on F; coordinator emits only compact progress.
    log=(folder/'run.log').open('a',encoding='utf8',buffering=1);sys.stdout=log;sys.stderr=log
    started=time.time()
    load_slot=stage_slot(root,load_jobs,'load');write_slot=stage_slot(root,write_jobs,'write')
    try:
        if shutil.disk_usage(root).free<20*1024**3:raise RuntimeError('F trial volume has less than 20 GiB free')
        cached=cached_result_is_valid(root,ck,actions)
        if cached is not None:
            return dict(character=ck,status='baked',resumed=True,seconds=cached.get('seconds',0))
        save(folder/'state.json',dict(status='waiting_load',started=started,character=ck))
        load_slot.__enter__()
        save(folder/'state.json',dict(status='loading',started=started,character=ck))
        cfg,folder=stage(root,ck)
        a=options(root,folder,ck);save(folder/'effective_options.json',vars(a))
        original=B.char_config
        follow=tuple((cfg['experiment'].get('follow_rigids_by_character') or {}).get(ck,()))
        B.char_config=lambda key:dict(original(key),pmx=Path(cfg['pmx']),**({'follow_prefixes':follow} if follow else {}))
        out=folder/'anims';out.mkdir(exist_ok=True)
        with E.rig_inputs(B,ck,head_mesh=cfg['meshes']['head'],torso_mesh=cfg['meshes']['torso'],use_release_recipe=False) as rig:
            contract=R.apply(rig,dict(cfg['experiment'].get('families') or {},**(cfg['experiment'].get('families_by_character') or {}).get(ck,{})),twist_z_limit_deg=cfg['experiment'].get('twist_z_limit_deg'),bone_twist_lock=cfg['experiment'].get('bone_twist_lock',False),bone_twist_families=(cfg['experiment'].get('bone_twist_families_by_character') or {}).get(ck,cfg['experiment'].get('bone_twist_families')),keep_light_welds=cfg['experiment'].get('keep_light_welds',False),light_weld_ratio=cfg['experiment'].get('light_weld_ratio',.2),keep_zero_lock_families=tuple(cfg['experiment'].get('keep_zero_lock_families',())),keep_zero_lock_names=tuple((cfg['experiment'].get('keep_zero_lock_names_by_character') or {}).get(ck,())),family_tokens=(cfg['experiment'].get('family_tokens_by_character') or {}).get(ck),zero_lock_limit_deg=(cfg['experiment'].get('zero_lock_limit_deg_by_character') or {}).get(ck),widen_limits_deg=(cfg['experiment'].get('widen_limits_deg_by_character') or {}).get(ck),mass_floor=(cfg['experiment'].get('mass_floor_by_character') or {}).get(ck),linear_limit_scale=(cfg['experiment'].get('linear_limit_scale_by_character') or {}).get(ck),joint_frame_limits=(cfg['experiment'].get('joint_frame_limits_by_character') or {}).get(ck));contract['follow_rigids_excluded']=sorted(rig.follow_rigids);save(folder/'response_contract.json',contract)
            scene=rig._scene_text
            def scene_response(self,*args,**kwargs):
                text,info=scene(*args,**kwargs)
                if cfg['experiment'].get('hair_torso_walls'):
                    text,walls=R.hair_torso_walls(text,self.model,contract)
                    contract['hair_torso_walls']=walls;save(folder/'response_contract.json',contract)
                rules=cfg['experiment'].get('exclude_pairs',{}).get(ck)
                if rules:
                    text,rows=R.exclude_pairs(text,self.model,contract,rules)
                    if not rows:raise ValueError('exclude_pairs matched nothing for '+ck)
                    contract['excluded_pairs']=rows;save(folder/'response_contract.json',contract)
                rules=cfg['experiment'].get('admit_pairs',{}).get(ck)
                if rules:
                    text,rows=R.admit_pairs(text,self.model,contract,rules)
                    if not rows:raise ValueError('admit_pairs matched nothing for '+ck)
                    contract['admitted_pairs']=rows;save(folder/'response_contract.json',contract)
                # 按动作覆盖（2026-10-11）：action_overrides_by_character[ck][action] 与
                # leg_skin_fit_by_character[ck]（大腿胶囊按蒙皮拟合，可限定 actions）。只改本条场景与契约副本；
                # 两个键都缺省时这里什么都不做，旧角色场景逐字节不变。
                override=dict(((cfg['experiment'].get('action_overrides_by_character') or {}).get(ck) or {}).get(current['action']) or {})
                if leg_fit and (leg_spec.get('actions') is None or current['action'] in leg_spec['actions']):
                    override['rigid_fit']=dict(override.get('rigid_fit') or {},**{str(i):dict(size=f['size'],location=f['location']) for i,f in leg_fit.items()})
                if override:
                    text,used,rows=R.action_override(text,self.model,contract,override)
                    contract.setdefault('action_overrides',{})[current['action']]=dict(
                        joints=len(rows['joints']),rigids=rows['rigids'],
                        follow_fam_removed_joints=rows.get('follow_fam_removed_joints',0))
                    save(folder/'response_contract.json',contract)
                    return R.append_scene_contract(text,used),info
                return R.append_scene_contract(text,contract),info
            current=dict(action=None)
            leg_spec=(cfg['experiment'].get('leg_skin_fit_by_character') or {}).get(ck) or {}
            leg_fit=None
            if leg_spec:
                import leg_skin_fit as LF
                leg_fit=LF.symmetrize(LF.fit(rig.model,tuple(leg_spec.get('names',('左足','右足'))),pct=leg_spec.get('pct',75.)),rig.model)
                if not leg_fit:raise ValueError('leg_skin_fit matched nothing for '+ck)
                contract['leg_skin_fit']={str(i):f for i,f in leg_fit.items()};save(folder/'response_contract.json',contract)
            rig._scene_text=MethodType(scene_response,rig)
            head_names=O.from_mesh(cfg['meshes']['head'],protected_names=()).bone_names
            observable=O.from_mesh(cfg['meshes']['head'],protected_names=head_names,reference_paths=[])
            prepared=[]
            previous_inputs=read(folder/'prepared.json')['files'] if (folder/'prepared.json').exists() else None
            with E.prepare_sources(rig,head_source_dir=cfg['source_dir'],observable_contract=observable):
                for action in actions:
                    current['action']=action
                    p=rig.prepare_one(Path(cfg['source_dir'])/(rig.cfg['body_prefix']+action+'.anim'),a)
                    p['loop']=is_loop(ck,action);prepared.append(p)
            native_inputs={str(p[k]):sha(p[k]) for p in prepared for k in ('sp','fp')}
            save(folder/'prepared.json',dict(status='prepared',files=native_inputs,loops={x['base']:x['loop'] for x in prepared}))
            load_slot.__exit__()
            # A role's actions go to the solver in gpu_chunks native batches; each
            # chunk takes its own GPU slot, so peak VRAM per solve drops with it.
            size=-(-len(prepared)//max(1,gpu_chunks))
            chunks=[prepared[i:i+size] for i in range(0,len(prepared),size)]
            cmds=[];procs={};all_reused=True
            for ci,chunk in enumerate(chunks):
                manifest=folder/('io/batch.tsv' if len(chunks)==1 else f'io/batch_{ci}.tsv')
                manifest.write_text('\n'.join('\t'.join([str(p[k]) for k in ('sp','fp','op')]+[str(int(p['loop']))]) for p in chunk)+'\n',encoding='utf8')
                single=rig.solver_command(chunk[0],a);cmd=single[:1]+['--batch',str(manifest)]+[v for v in single[4:] if v!='--loop']
                cmds.append(cmd)
                reusable=(previous_inputs==native_inputs and all(
                    p['op'].is_file() and Path(str(p['op'])+'.command.json').exists() and
                    Path(str(p['op'])+'.solver.json').exists() for p in chunk))
                if reusable:
                    for p in chunk:
                        command=read(str(p['op'])+'.command.json')
                        report=read(str(p['op'])+'.solver.json')
                        with Path(p['fp']).open('rb') as stream:source_frames=struct.unpack('<ii',stream.read(8))[0]
                        tail=min(24,source_frames//2) if p['loop'] and source_frames>=4 else 0
                        error=AC.raw_output_error(output=p['op'],frames=p['fp'],rigids=len(rig.model.rigids),
                            tail_frames=tail,report=report,command_record=command,
                            solver_sha256=cfg['native_sha256'],expected_command=cmd,
                            batch_instances=len(chunk))
                        if error:
                            reusable=False;break
                if reusable:
                    import subprocess
                    proc=subprocess.CompletedProcess(cmd,0,command['stdout'],command['stderr'])
                else:
                    all_reused=False
                    save(folder/'state.json',dict(status='waiting_gpu',started=started,character=ck,chunk=ci,chunks=len(chunks)))
                    with stage_slot(root,gpu_jobs,'gpu') as slot:
                        save(folder/'state.json',dict(status='solving',started=started,character=ck,chunk=ci,chunks=len(chunks)))
                        # Multi-GPU host: slot i solves on card i % N. Only the child
                        # environment changes, so the recorded command stays identical.
                        if gpu_devices>1:os.environ['CUDA_VISIBLE_DEVICES']=str(slot.index%gpu_devices)
                        proc=rig.run_solver(cmd,chunk)
                for p in chunk:procs[p['base']]=proc
            reusable=all_reused
            save(folder/'state.json',dict(status='waiting_write',started=started,character=ck))
            write_slot.__enter__()
            save(folder/'state.json',dict(status='writing_back',started=started,character=ck))
            batch_cmd=cmds[0] if len(cmds)==1 else cmds
            save(folder/'io/batch_command.json',dict(command=batch_cmd,solver_sha256=cfg['native_sha256'],
                input_hashes=native_inputs,actions=list(actions),effective_options=vars(a),
                chunks=[[p['base'] for p in chunk] for chunk in chunks]))
            for p,h in native_inputs.items():
                if sha(p)!=h:raise ValueError('Native input changed during solve')
            # PMX can contain self-joints. Native deliberately skips these; do
            # not silently treat one as a real spring, or reject valid raw output.
            invalid={i for i,j in enumerate(rig.model.joints) if j.src_rigid is None or j.dest_rigid is None
                     or j.src_rigid<0 or j.dest_rigid<0 or j.src_rigid==j.dest_rigid}
            skipped_response=[j['index'] for j in contract['joints'] if j['index'] in invalid]
            save(folder/'native_joint_coverage.json',dict(authored_invalid_joints=sorted(invalid),
                skipped_response_joints=skipped_response,effective_response_count=len(contract['joints'])-len(skipped_response)))
            results={};failures=[];reports=[]
            for p in prepared:
                rep=read(str(p['op'])+'.solver.json')
                if rep['sdk_errors_or_warnings']!=0:raise ValueError('SDK error '+p['base'])
                # follow_families 动作覆盖：被改成 kinematic 的刚体关节不建 response drive，
                # 需从期望数里减去当前动作被移除的关节数，否则校验必然不过。
                # action_overrides 的 key 是动作名（如 'sitting'），p['base'] 是文件名前缀
                # （如 'moe_wcq_body_sitting'），需要去掉 body_prefix 才能匹配。
                _base_action=p['base']
                _bp=rig.cfg.get('body_prefix','')
                if _bp and _base_action.startswith(_bp):
                    _base_action=_base_action[len(_bp):]
                _ao=(contract.get('action_overrides') or {}).get(_base_action) or {}
                _follow_removed=_ao.get('follow_fam_removed_joints',0)
                if rep['response_joints']!=len(contract['joints'])-len(skipped_response)-_follow_removed or rep['skipped_invalid_joints']!=len(invalid):
                    raise ValueError('Response contract count mismatch')
                if rep.get('twist_z_joints',0)!=len(contract.get('authorized_twist_z_limits',[])):
                    raise ValueError('Twist Z contract count mismatch')
                if rep.get('twist_z_locked_joints',0)!=contract.get('twist_z_locked_joints',0):
                    raise ValueError('Twist Z lock count mismatch')
                if rep.get('bone_twist_locked_bodies',0)!=len(contract.get('bone_twist_locks',[])):
                    raise ValueError('Bone local Z lock count mismatch')
                if (contract.get('bone_twist_lock') and getattr(a,'physx_bone_twist_projection',True)
                        and rep['max_bone_twist_after_projection_deg']>=.02):
                    raise ValueError('Bone local Z lock did not converge')
                reports.append(dict(action=p['base'],response_joints=rep['response_joints'],
                    slerp=rep['response_slerp_joints'],axis=rep['response_axis_joints'],max_linear_error=rep['max_linear_limit_error_pmx']))
                try:results[p['base']]=rig.finish_one(p,out,a,procs[p['base']])
                except Exception as e:failures.append(dict(action=p['base'],error=str(e)))
            expected={rig.cfg[part+'_prefix']+act+'.anim' for part in ('head','body') for act in actions}
            actual={p.name for p in out.glob('*.anim')}
            if actual!=expected:failures.append(dict(error='Output coverage mismatch',missing=sorted(expected-actual),extra=sorted(actual-expected)))
            for p in out.glob('*.anim'):
                src=Path(cfg['source_dir'])/p.name;sm,sb,_=B.RV.load_anim(src);dm,db,ds=B.RV.load_anim(p)
                if sm['sa']!=dm['sa'] or sm['fps']!=dm['fps'] or [b['name'] for b in sb]!=[b['name'] for b in db]:
                    raise ValueError('Writeback timing/bone table changed: '+p.name)
                frames,ts,qs=B.RV.unpack_samples(dm,db,ds)
                if any(not np.isfinite(q).all() for q in qs.values()):raise ValueError('Nonfinite output')
            result=dict(character=ck,status='baked' if not failures else 'rejected',seconds=time.time()-started,
                results=results,failures=failures,reports=reports,output_hashes={str(p):sha(p) for p in out.glob('*.anim')},
                accepted=False,installed=False,visual_review='pending',native_reused=reusable)
            result['response_contract_sha256']=sha(folder/'response_contract.json')
            if not failures:
                result['artifact_fingerprint']=AC.canonical_sha256(dict(configuration=cfg,
                    effective_options=vars(a),prepared=native_inputs,
                    contract_sha256=result['response_contract_sha256'],outputs=result['output_hashes'],
                    batch_command=batch_cmd))
            save(folder/'results.json',result);save(folder/'state.json',dict(status=result['status'],character=ck,seconds=result['seconds']))
            return dict(character=ck,status=result['status'],seconds=result['seconds'],failures=failures)
    except BaseException as e:
        traceback.print_exc();result=dict(character=ck,status='failed',error=str(e),seconds=time.time()-started)
        save(folder/'state.json',result);return result
    finally:
        load_slot.__exit__();write_slot.__exit__();log.flush()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True,type=Path);ap.add_argument('--characters',nargs='+');ap.add_argument('--jobs',type=int,default=2)
    ap.add_argument('--gpu-jobs',type=int,default=0,help='max workers inside the native GPU solve at once (0 = no cap)')
    ap.add_argument('--load-jobs',type=int,default=0,help='max workers staging/preparing inputs at once (0 = no cap)')
    ap.add_argument('--write-jobs',type=int,default=0,help='max workers writing back animations at once (0 = no cap)')
    ap.add_argument('--gpu-chunks',type=int,default=1,help='split each role into N native solver batches, each taking its own GPU slot')
    ap.add_argument('--gpu-devices',type=int,default=0,help='spread GPU slots over N CUDA devices (slot i -> device i %% N; 0/1 = default device)')
    ap.add_argument('--resume',action='store_true',help='resume an existing paused/finished queue after validating completed character artifacts')
    a=ap.parse_args();root=a.root.resolve()
    if root.is_relative_to(ROOT/'mod') or root==ROOT:raise ValueError('Trial root must be isolated')
    checks=read(root/'native_verified_v1/verification.json');extra=read(root/'native_verified_v1/extended/verification.json')
    assert checks['status']==extra['status']=='passed' and checks['exe_sha256']==sha(root/'native/moe_physx_response.exe')
    if (root/'experiment.json').exists() and read(root/'experiment.json').get('bone_twist_lock'):
        lock=read(root/'native_bone_twist/verification.json')
        assert lock['status']=='passed' and lock['exe_sha256']==checks['exe_sha256']
    if (root/'experiment.json').exists() and read(root/'experiment.json').get('twist_z_limit_deg') is not None:
        twist=read(root/'native_twist_z/verification.json')
        assert twist['status']=='passed' and twist['exe_sha256']==checks['exe_sha256']
        if read(root/'experiment.json')['twist_z_limit_deg']==0:
            lock=read(root/'native_twist_z_lock/verification.json')
            assert lock['status']=='passed' and lock['exe_sha256']==checks['exe_sha256']
    inventory=read(root/'inventory.json');cks=a.characters or [r['character'] for r in inventory['characters']]
    scope=read(root/'trial_scope.json').get('actions') if (root/'trial_scope.json').exists() else None
    actions=('all',) if scope=='all' else trial_actions(root)
    if len(cks)!=len(set(cks)) or not set(cks)<={r['character'] for r in inventory['characters']}:raise ValueError('Invalid role list')
    queue_path=root/'queue.json';old_queue=read(queue_path) if queue_path.exists() else None
    if old_queue is not None and not a.resume:
        raise ValueError('Trial queue already exists; pass --resume or choose a fresh output root')
    if old_queue is not None:
        if old_queue.get('status')=='running':
            raise ValueError('Queue still says running; confirm the prior process is stopped before resuming')
        if old_queue.get('actions')!=list(actions) or not set(cks)<=set(old_queue.get('characters',[])):
            raise ValueError('Resume scope differs from the existing queue; choose a fresh output root')
    resumed=[];pending=[]
    for ck in cks:
        cached=cached_result_is_valid(root,ck,trial_actions(root,ck))
        if cached is None:pending.append(ck)
        else:resumed.append(dict(character=ck,status='baked',resumed=True,seconds=cached.get('seconds',0)))
    run_id=AC.canonical_sha256(dict(experiment=read(root/'experiment.json'),actions=list(actions),
        characters=list(cks),solver=checks['exe_sha256']))
    results=list(resumed)
    save(root/'queue.json',dict(schema=2,run_id=run_id,status='running' if pending else 'finished',
        characters=cks,actions=list(actions),results=results,started=time.time(),pending=pending))
    with ProcessPoolExecutor(max_workers=max(1,a.jobs)) as pool:
        futures={pool.submit(worker,str(root),ck,a.gpu_jobs,a.load_jobs,a.write_jobs,a.gpu_chunks,a.gpu_devices):ck for ck in pending}
        for future in as_completed(futures):
            r=future.result();results.append(r);print(json.dumps(r,ensure_ascii=False),flush=True)
            save(root/'queue.json',dict(schema=2,run_id=run_id,status='running',characters=cks,
                actions=list(actions),results=results,started=time.time(),pending=[x for x in pending
                if x not in {r.get('character') for r in results}]))
    save(root/'queue.json',dict(schema=2,run_id=run_id,status='finished',characters=cks,
        actions=list(actions),results=results,finished=time.time(),pending=[]))
    if any(r['status']!='baked' for r in results):sys.exit(1)


if __name__=='__main__':main()
