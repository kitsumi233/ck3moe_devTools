"""Bone-local longitudinal twist contract, independent of PMX cross-links.

Frames use the destination mesh bind orientation. Each dynamic rigid receives
one relative angular constraint to its actual bone parent, never one per cloth
cross-link. The native solver enforces the constraint during every substep.
"""
import numpy as np
import moe_bullet_bake as B


def contract(rig):
    model=rig.model
    # Writeback stores one pose per mesh bone and the last dynamic rigid mapped
    # to a mesh bone is authoritative. Other dynamic colliders still need their
    # own twist lock, based on their source PMX bone axis.
    by_name={name:i for i,name in enumerate(rig.rigid_bone)
             if name is not None and model.rigids[i].mode!=0}
    by_bone={r.bone:i for i,r in enumerate(model.rigids) if r.bone is not None and r.bone>=0}
    rows=[]
    for child,r in enumerate(model.rigids):
        if r.mode==0:continue
        if r.bone is None or r.bone<0:
            # Bone-less dynamic links (鸣潮 breast chains 左胸_後/_回転/…, 2026-10-09) drive no
            # mesh bone and have no axis to lock; they only pass forces through joints.
            # None of the 92 released roles has one, so their contracts are unchanged.
            continue
        bone=model.bones[r.bone];mapped_name=rig.rigid_bone[child]
        is_mesh_driver=(mapped_name is not None and by_name.get(mapped_name)==child)
        name=mapped_name if is_mesh_driver else None
        if name is not None:
            basis=np.asarray(rig.bind[name][0],dtype=float)
            # Bind matrices originate in float mesh files. Remove roundoff,
            # preserving the real three bone axes, rather than assuming XYZ.
            u,_,vt=np.linalg.svd(basis);basis=u@vt
            if np.linalg.det(basis)<0:raise ValueError('Reflected bind: '+name)
            parent_name=rig.parents.get(name)
            parent=by_name.get(parent_name,-1)
            parent_bone=rig.ours_to_pmx_idx.get(parent_name,-1)
            origin='mesh_bind_local_z'
        else:
            direction=bone.displayConnection
            if isinstance(direction,int):
                direction=np.asarray(model.bones[direction].location)-bone.location if direction>=0 else np.zeros(3)
            elif direction is None:direction=np.zeros(3)
            direction=np.asarray(direction,dtype=float)
            if np.linalg.norm(direction)<1e-7:
                direction=B.q_to_m(B.euler_yxz_q(r.rotation))[:,1]
            z=direction/np.linalg.norm(direction)
            ref=np.eye(3)[np.argmin(abs(z))];x=np.cross(ref,z);x/=np.linalg.norm(x)
            basis=np.column_stack((x,np.cross(z,x),z))
            parent_bone=bone.parent if bone.parent is not None else -1
            parent=by_bone.get(parent_bone,-1)
            parent_name=None;origin='unskinned_bone_longitudinal_axis'
        if parent==child:raise ValueError('Self bone twist '+r.name)
        if parent<0 and parent_bone<0:raise ValueError('No parent reference for '+r.name)
        q=B.ms_to_qs(basis[None])[0];q=q/np.linalg.norm(q)
        rows.append(dict(child=child,parent=parent,parent_bone=parent_bone,name=r.name,
            bone=name,bone_index=r.bone,parent_name=parent_name,frame_quaternion=q.tolist(),
            axis=basis[:,2].tolist(),frame_source=origin,mode='locked',limit_deg=0,
            mesh_driver=is_mesh_driver,mapped_mesh_bone=mapped_name))
    # Topological order makes the exact native correction respect the final
    # corrected parent orientation; this is also checked natively.
    pending={r['child']:r for r in rows};ordered=[];visiting=set()
    def visit(child):
        if child not in pending:return
        if child in visiting:raise ValueError('Cyclic bone twist hierarchy')
        visiting.add(child);row=pending[child];visit(row['parent'])
        ordered.append(pending.pop(child));visiting.remove(child)
    for child in list(pending):visit(child)
    return ordered


def append(scene,rows):
    return scene+''.join('bone_twist %d %d %d %s\n' %
        (r['child'],r['parent'],r['parent_bone'],' '.join('%.10g'%x for x in r['frame_quaternion'])) for r in rows)
