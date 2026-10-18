# -*- coding: utf-8 -*-
"""原生 Bullet 物理烘焙的 Python 侧：读 PMX 与 .anim → 喂 moe_bullet_bake.exe → 写回 .anim。

为什么有这个文件（2026-09-05 与用户定案，见 todo.md 第 3 节）：Blender 那条路
（bake_hair_bullet.py）的碰撞过滤是引擎层锁死的，只能逐对建 NonCollision 约束（艾尔黛拉
107284 对、装配 1330 s），重力/休眠/关节类型/步长也都是替代品。这里 Blender **彻底退出**：
  · 输入直读 PMX 原文（mmd_tools 的 core/pmx 解析器纯 Python，不依赖 bpy）：刚体尺寸用原文
    radius/height、关节位置朝向用原文、质量原值、24 个模式 0 刚体全当运动学墙
  · 解算是 tools/physics/moe_bullet/main.cpp，一比一照 saba 的 MMDPhysics.cpp
  · 本文件只做坐标换算、FK、写回、判据

## 坐标关系（实测定案，不是假设）

我们 .anim/.mesh 空间 = PMX 空间 × scale，轴序不变、无镜像：洛茜 .mesh 逆绑定反解出 head
在 (0, 114.057, 0.989)，PMX head × 7.191766 完全相等。PMX 骨 rest **无旋转**，我们的骨有绑定
朝向 R_bind，所以「同一根骨在 PMX 约定下的当帧世界变换」= [R_f · R_bind⁻¹ | p_f / scale]。
反向写回时乘回去。

## 写回规则（与 bake_hair_bullet 的 writes_q / writes_t 同义，但直接读 PMX mode）

  mode 0  运动学（骨→刚体）        不写
  mode 1  物理（刚体→骨）          写 q 与 t
  mode 2  物理+骨位置合わせ        只写 q，t 走 FK
q、t 的参考系都是**父骨当帧**（父骨若也由刚体驱动就用它的解，否则用 FK），
见 [[anim-q-and-t-share-parent-current-frame]]。t 沿 rest 骨轴的分量去掉（保长），照旧。

## 后处理

只保留与「CK3 循环回放」有关的接缝处理：解算器对循环动画多录下一圈开头 K 帧（--loop-extra），
本圈开头与它做 C2 交叉淡入，接缝两侧使用相邻的真实物理采样；最终骨动画仍需检查首尾速度，
不能仅由风场周期性宣称循环通过。短动作自动缩短续录窗口。夹持、低通、后贴正弦扰动都不做。

用法:
  python tools/physics/moe_bullet_bake.py --ck lau --anim stayback_start --out-dir work/bullet_native/lau
  python tools/physics/moe_bullet_bake.py --ck lau --all --out-dir work/bullet_native/lau --jobs 12

--jobs>1 走进程池（每 worker 自装 Rig）：ThreadPool 共享 GIL，Python 后处理占一半时间时 12 job 只能同时跑 6~7 个
解算器（2026-09-07 用户「负载没拉满」）。--anim 可重复给多条，不是逗号分隔。
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_bone_contract as MC          # noqa: E402
import moe_paths as MP                  # noqa: E402
import retarget_vanilla_anim as RV      # noqa: E402
import anim_writeback as BPI            # noqa: E402
from physics_profile import DEFAULT_SOLVER  # CLI 与调试台共用驱动基线

EXE = ROOT / "work" / "moe_bullet_build" / "moe_bullet_bake.exe"
PHYSX_EXE = ROOT / "work" / "moe_physx_build" / "moe_physx_bake.exe"
PMX_PARSER = Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender" / "5.2" \
    / "extensions" / "user_default" / "mmd_tools" / "core" / "pmx" / "__init__.py"

# 角色 → 导入 report（scale 与 PMX 路径的唯一真值来源）。mesh 名与前缀由 moe_paths 推。
import moe_chars as X
REPORTS = {c["ck"]: str(X.report_path(c["ck"]).relative_to(ROOT)) for c in X.CHARS}


GROUP_KEYS = ("tail", "skirt", "hair", "cloak", "cape", "ear", "hat", "paw")


def log(m):
    print(m, flush=True)


# ------------------------------------------------------------------ PMX
def load_pmx_module():
    if not PMX_PARSER.is_file():
        raise SystemExit(f"**找不到 mmd_tools 的 PMX 解析器** {PMX_PARSER}")
    spec = importlib.util.spec_from_file_location("mmd_pmx", PMX_PARSER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def char_config(ck):
    rep = json.loads((ROOT / REPORTS[ck]).read_text(encoding="utf-8"))
    scale = float(rep["scale"]["factor"])
    pmx = Path(rep["source_pmx"])
    if not pmx.is_file():
        # 芙洛伦 report 里的绝对路径已失效（素材搬过家），按文件名回搜。
        hits = list((ROOT / "assets" / "source_models").rglob(pmx.name))
        if not hits:
            raise SystemExit(f"**{ck} 的 PMX 找不到**：{pmx}")
        pmx = hits[0]
    prefix = MP.CHARACTERS[ck]["prefix"]
    head, torso = f"{prefix}head.mesh", f"{prefix}torso.mesh"
    return {"ck": ck, "scale": scale, "pmx": pmx, "head": head, "torso": torso,
            "body_prefix": prefix + "body_", "head_prefix": prefix + "head_"}


# ------------------------------------------------------------------ 数学
def q_to_m(q):
    """(x,y,z,w) → 3x3。"""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def qs_to_ms(qs):
    """(N,4) (x,y,z,w) → (N,3,3)，批量。"""
    q = np.asarray(qs, dtype=np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    m = np.empty((len(q), 3, 3))
    m[:, 0, 0] = 1 - 2 * (y * y + z * z); m[:, 0, 1] = 2 * (x * y - z * w); m[:, 0, 2] = 2 * (x * z + y * w)
    m[:, 1, 0] = 2 * (x * y + z * w); m[:, 1, 1] = 1 - 2 * (x * x + z * z); m[:, 1, 2] = 2 * (y * z - x * w)
    m[:, 2, 0] = 2 * (x * z - y * w); m[:, 2, 1] = 2 * (y * z + x * w); m[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def ms_to_qs(m):
    """(N,3,3) → (N,4) (x,y,z,w)，Shepperd 法批量。"""
    m = np.asarray(m, dtype=np.float64)
    n = len(m)
    q = np.empty((n, 4))
    t = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    for i in range(n):
        a = m[i]
        if t[i] > 0:
            s = math.sqrt(t[i] + 1.0) * 2
            q[i] = [(a[2, 1] - a[1, 2]) / s, (a[0, 2] - a[2, 0]) / s, (a[1, 0] - a[0, 1]) / s, 0.25 * s]
        elif a[0, 0] > a[1, 1] and a[0, 0] > a[2, 2]:
            s = math.sqrt(1.0 + a[0, 0] - a[1, 1] - a[2, 2]) * 2
            q[i] = [0.25 * s, (a[0, 1] + a[1, 0]) / s, (a[0, 2] + a[2, 0]) / s, (a[2, 1] - a[1, 2]) / s]
        elif a[1, 1] > a[2, 2]:
            s = math.sqrt(1.0 + a[1, 1] - a[0, 0] - a[2, 2]) * 2
            q[i] = [(a[0, 1] + a[1, 0]) / s, 0.25 * s, (a[1, 2] + a[2, 1]) / s, (a[0, 2] - a[2, 0]) / s]
        else:
            s = math.sqrt(1.0 + a[2, 2] - a[0, 0] - a[1, 1]) * 2
            q[i] = [(a[0, 2] + a[2, 0]) / s, (a[1, 2] + a[2, 1]) / s, 0.25 * s, (a[1, 0] - a[0, 1]) / s]
    # 相邻帧符号对齐，否则 Blender 视口插值绕长路（[[quaternion-keyframe-sign-must-align]]）
    for i in range(1, n):
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] = -q[i]
    return q


def q_angle_deg(a, b):
    d = abs(float(np.dot(a, b)))
    return math.degrees(2 * math.acos(max(-1.0, min(1.0, d))))


def slerp(a, b, t):
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        r = a + (b - a) * t
    else:
        th0 = math.acos(min(1.0, d)); th = th0 * t
        r = a * (math.sin(th0 - th) / math.sin(th0)) + b * (math.sin(th) / math.sin(th0))
    return r / (np.linalg.norm(r) or 1.0)


def slerp_path(a, b, t):
    """与 slerp 相同但不取最短路径：调用方已为整段选定符号，逐帧不再换边。"""
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    d = max(-1.0, min(1.0, float(np.dot(a, b))))
    th0 = math.acos(d)
    if math.sin(th0) < 1e-4:
        r = a + (b - a) * t
    else:
        th = th0 * t
        r = a * (math.sin(th0 - th) / math.sin(th0)) + b * (math.sin(th) / math.sin(th0))
    return r / (np.linalg.norm(r) or 1.0)


# ------------------------------------------------------------------ mesh / anim
def mesh_bind(mesh_path):
    """{骨名: (R_bind 3x3, p_bind)}。tx 是列主序**逆**绑定矩阵（12 float）。"""
    import pdx_parse
    tree = pdx_parse.parse(Path(mesh_path).read_bytes())
    out = {}

    def walk(nd):
        for name, ch in RV.kids(nd):
            pr = RV.props(ch)
            if "ix" in pr and "tx" in pr and len(pr["tx"][1]) >= 12:
                tx = pr["tx"][1]
                M = np.array([[tx[0], tx[3], tx[6]], [tx[1], tx[4], tx[7]], [tx[2], tx[5], tx[8]]])
                t = np.array(tx[9:12])
                out[name] = (M.T, -M.T @ t)
            walk(ch)
    walk(tree)
    return out


def fk_frames(F, rest, ts, qs, parents, order):
    """逐帧 FK（.anim 空间）。返回 {骨名: (R (F,3,3), p (F,3))}。
    无通道的骨用 .anim 自己的 rest（不是 mesh rest，见 check_head_torso_sync 的注释）。"""
    W = {}
    for n in order:
        b = rest[n]
        Rl = qs_to_ms(qs[n]) if n in qs else np.repeat(q_to_m(b["q"])[None], F, axis=0)
        tl = np.asarray(ts[n], dtype=np.float64) if n in ts else np.repeat(np.asarray(b["t"], dtype=np.float64)[None], F, axis=0)
        pn = parents.get(n)
        if pn is None or pn not in W:
            W[n] = (Rl, tl)
        else:
            Rp, pp = W[pn]
            W[n] = (Rp @ Rl, pp + np.einsum("fij,fj->fi", Rp, tl))
    return W


def _orthonormal(m):
    u, _, vt = np.linalg.svd(np.asarray(m, dtype=np.float64))
    return u @ vt


def writeback_twist(Rb, parents, bind, W, detwist=False):
    """模拟骨相对（最终写回的）父骨的纵向局部 Z 自转，两者都从 bind 起算。

    与原生 bone_twist 锁同一定义：Q = C0ᵀ·(P·P0ᵀ)ᵀ·C，自转 = 2·atan2(Q.z, Q.w)。
    detwist=True 时把 C 换成 G·C0·swing：只去掉自转，骨轴方向与骨位置不变。
    父骨先处理（拓扑序），所以子骨参照的是已去自转的父骨。原地修改 Rb。"""
    before, after = [], []
    for n in topo_order(list(Rb), parents):
        C = Rb[n]
        C0 = _orthonormal(bind[n][0])
        pn = parents.get(n)
        if pn is None:
            G = np.repeat(np.eye(3)[None], len(C), axis=0)
        else:
            P = Rb[pn] if pn in Rb else W[pn][0]
            G = np.einsum("fij,kj->fik", P, _orthonormal(bind[pn][0]))    # P·P0ᵀ
        Q = np.einsum("ji,fkj,fkl->fil", C0, G, C)                         # C0ᵀ·Gᵀ·C
        q = ms_to_qs(Q)
        tw = np.abs(np.remainder(2 * np.arctan2(q[:, 2], q[:, 3]) + np.pi, 2 * np.pi) - np.pi)
        before.append(np.degrees(tw))
        if not detwist:
            after.append(np.degrees(tw))
            continue
        for i in range(1, len(q)):             # 符号对齐后半角可展开成连续信号
            if (q[i] * q[i - 1]).sum() < 0:
                q[i] = -q[i]
        den = np.sqrt(q[:, 2] ** 2 + q[:, 3] ** 2)
        # 摆角接近 180°（den 小）时绕骨轴的自转分解是奇异的：刚体转几度，分解出的自转就能跳一百多度，
        # 去掉它等于把这份跳变写回骨头（林 laugh/dance_loop 后发 138~167°/帧；逐帧插值、时间平滑都压不住）。
        # 这种骨整段不去自转：原生 bone_twist 锁已在解算里约束了自转，这里只是事后清理。
        if den.min() < 0.5:
            after.append(np.degrees(tw))
            continue
        half = np.unwrap(2 * np.arctan2(q[:, 2], q[:, 3])) / 2
        tz, tw_ = np.sin(half), np.cos(half)
        # swing = q·conj(t)，t = (0,0,tz,tw_)
        x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
        swing = np.stack([x * tw_ - y * tz, y * tw_ + x * tz, z * tw_ - w * tz, w * tw_ + z * tz], axis=1)
        Rb[n] = np.einsum("fij,jk,fkl->fil", G, C0, qs_to_ms(swing))
        q2 = ms_to_qs(np.einsum("ji,fkj,fkl->fil", C0, G, Rb[n]))
        after.append(np.degrees(np.abs(np.remainder(2 * np.arctan2(q2[:, 2], q2[:, 3]) + np.pi, 2 * np.pi) - np.pi)))
    b = np.concatenate(before) if before else np.zeros(1)
    c = np.concatenate(after) if after else np.zeros(1)
    return dict(detwist=bool(detwist), bones=len(before), before_max_deg=float(b.max()),
                before_p95_deg=float(np.percentile(b, 95)), after_max_deg=float(c.max()),
                after_p95_deg=float(np.percentile(c, 95)))


def _lowpass_gain(F, fps, cutoff_hz):
    """Zero-phase gain per rFFT bin: 1 below 0.7·cutoff, raised-cosine to 0 at 1.3·cutoff."""
    f = np.fft.rfftfreq(F, 1 / fps)
    lo, hi = 0.7 * cutoff_hz, 1.3 * cutoff_hz
    g = np.clip((hi - f) / (hi - lo), 0, 1)
    return 0.5 - 0.5 * np.cos(np.pi * g)


def lowpass_series(x, fps, cutoff_hz, loop):
    """Zero-phase low-pass of a (F, k) series. Loops are filtered circularly (the seam
    is already continuous); one-shot clips are mirror-padded so ends keep their values."""
    F = len(x)
    if F < 8:
        return x
    if loop:
        return np.fft.irfft(np.fft.rfft(x, axis=0) * _lowpass_gain(F, fps, cutoff_hz)[:, None], n=F, axis=0)
    pad = min(F - 1, int(np.ceil(2 * fps / cutoff_hz)))
    # odd reflection keeps value and slope continuous at both ends
    head = 2 * x[:1] - x[pad:0:-1]
    tail = 2 * x[-1:] - x[-2:-pad - 2:-1]
    y = np.concatenate([head, x, tail])
    n = len(y)
    y = np.fft.irfft(np.fft.rfft(y, axis=0) * _lowpass_gain(n, fps, cutoff_hz)[:, None], n=n, axis=0)
    return y[pad:pad + F]


def lowpass_local_rotations(frames_q, moves, fps, cutoff_hz, loop):
    """Low-pass each simulated bone's parent-local rotation and translation. Only
    physics-written channels are touched; body/FK is unchanged. Rotations are filtered
    as sign-aligned quaternion components then renormalised: a log map about the mean
    wraps at 180° (lin idle tail swings ±180°) and the filter smeared that jump into
    a 263° artefact. Returns stats of the removed motion in degrees."""
    removed = []
    for n, ql in frames_q.items():
        q = np.asarray(ql, dtype=np.float64)
        for i in range(1, len(q)):
            if (q[i] * q[i - 1]).sum() < 0:
                q[i] = -q[i]
        if loop and (q[0] * q[-1]).sum() < 0:
            continue    # sign-aligned path does not close the loop (full turn): leave as solved
        qf = lowpass_series(q, fps, cutoff_hz, loop)
        nrm = np.linalg.norm(qf, axis=1, keepdims=True)
        if nrm.min() < 0.5:
            continue    # averaging opposite orientations: filter is meaningless here
        qf /= nrm
        removed.append(np.degrees(2 * np.arccos(np.clip(np.abs((q * qf).sum(1)), 0, 1))))
        frames_q[n] = [tuple(t) for t in qf]
    for n, tl in moves.items():
        moves[n] = [tuple(t) for t in lowpass_series(np.asarray(tl, dtype=np.float64), fps, cutoff_hz, loop)]
    d = np.concatenate(removed) if removed else np.zeros(1)
    return dict(cutoff_hz=cutoff_hz, bones=len(frames_q), removed_p50_p95_max_deg=[
        float(np.median(d)), float(np.percentile(d, 95)), float(d.max())])


def topo_order(names, parents):
    seen, out = set(), []

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        p = parents.get(n)
        if p in names_set:
            visit(p)
        out.append(n)
    names_set = set(names)
    for n in names:
        visit(n)
    return out


_LOOP = None
_LOOP_BASE = {}


def is_looping(anim_id):
    """照 bake_hair_bullet.is_looping（那边顶层 import bpy，抄一份）。查不到当循环。

    键是 .asset 里的 **animation id**（= .anim 文件名 stem，如 `moe_lau_body_stayback_start`），
    不是剥掉前缀的 state 名。2026-09-05 第一版传裸名 `stayback_start` 查不到 → 默认循环 →
    入场动画被当循环跑 3 圈并闭合接缝，运动学墙从末帧瞬移回首帧把挂件撞飞：
    艾尔黛拉 skirt_2_19 记录帧 0 距离 11.6（rest 3.23），之后 8 帧内落回 3.64。
    表要遍历**全部**模型子 mod 的 .asset —— 只读第一份时洛茜/佩利卡的键根本不在表里。
    键再剥掉角色前缀归一成 `body_stayback_start`：state 表是按原版清单抄的、与角色无关，
    而艾尔黛拉这轮还没登记进 .asset（endfield 里 0 条 moe_eld_ animation），
    只能借别的角色的表 —— 借不到才退回默认循环。
    """
    global _LOOP
    if _LOOP is None:
        _LOOP = {}
        for ap_ in (MP.assets() or []):
            if not ap_.is_file():
                continue
            txt = ap_.read_text(encoding="utf-8-sig", errors="ignore")
            for m in re.finditer(r"state\s*=\s*\{", txt):
                i, d = m.end(), 1
                while i < len(txt) and d:
                    d += (txt[i] == "{") - (txt[i] == "}")
                    i += 1
                blk = txt[m.end():i - 1]
                nm = re.search(r'name\s*=\s*"([^"]+)"', blk)
                if not nm:
                    continue
                ns = re.search(r'next_state\s*=\s*"([^"]+)"', blk)
                lp = re.search(r"looping\s*=\s*(yes|no)", blk)
                _ns, _self = (ns.group(1) if ns else ""), nm.group(1)
                loop = (_ns == _self or _ns.rstrip("0123456789") == _self
                        or _self.rstrip("0123456789") == _ns or bool(lp and lp.group(1) == "yes"))
                for a in re.findall(r'animation\s*=\s*"([^"]+)"', blk):
                    k = _strip_char_prefix(a)
                    _LOOP[k] = _LOOP.get(k, False) or loop
                    # 载体架构的 asset 里 animation 是 `body_X__<head 名>[__rig]` 合并 id，而查询键是
                    # 裸 `body_X`：不归一就永远查不到 → 全部当循环（10 个固定动作靠常量表躲过，
                    # 全动作烘焙里 123 个非循环动作会被多圈+闭合接缝）。归一表单独放，
                    # 不动 _LOOP（1710 行与预览器按原键求交集）。
                    b = k.split("__", 1)[0]
                    _LOOP_BASE[b] = _LOOP_BASE.get(b, False) or loop
    k = _strip_char_prefix(anim_id)
    return _LOOP.get(k, _LOOP_BASE.get(k.split("__", 1)[0], True))


def _strip_char_prefix(anim_id):
    """`moe_per_body_stayback_start` → `body_stayback_start`（最长前缀优先，`moe_` 是兜底）。"""
    for pre in sorted((c["prefix"] for c in MP.CHARACTERS.values()), key=len, reverse=True):
        if anim_id.startswith(pre):
            return anim_id[len(pre):]
    return anim_id


# ------------------------------------------------------------------ 驱动放大
DRIVE_T_BONES = ("body_root",)
DRIVE_Q_BONES = ("bn_m_spine_low", "bn_m_spine_mid", "bn_m_spine_high", "bn_m_neck", "bn_m_head")


def q_pow(dq, g):
    """单位四元数 (x,y,z,w) 的 g 次幂：转角乘 g、轴不变。"""
    x, y, z, w = dq
    w = max(-1.0, min(1.0, float(w)))
    s_ = math.sqrt(max(0.0, 1.0 - w * w))
    if s_ < 1e-9:
        return (0.0, 0.0, 0.0, 1.0)
    h = math.acos(w) * g
    return (x / s_ * math.sin(h), y / s_ * math.sin(h), z / s_ * math.sin(h), math.cos(h))


def amplify_drive(ts, qs, rest, F, gain, thresh):
    """待机飘动的风场替代：返回只给 FK/解算用的 (ts', qs', g)。写回身体通道仍用原 ts/qs。
    body_root 位移与脊椎链旋转围绕时间均值放大 g 倍；g 按 body_root 位移峰自适应：
    ≤ thresh 取满 gain，≥ 2·thresh 为 1（舞蹈/入场类不放大）。"""
    if gain is None or gain <= 1.0:
        return ts, qs, 1.0
    D = 0.0
    if "body_root" in ts:
        A = np.asarray(ts["body_root"], dtype=np.float64)
        D = float(np.linalg.norm(A - A.mean(0), axis=1).max())
    k = min(1.0, max(0.0, (2.0 * thresh - D) / thresh))
    g = 1.0 + (gain - 1.0) * k
    if g <= 1.0 + 1e-6:
        return ts, qs, 1.0
    ts2, qs2 = dict(ts), dict(qs)
    for n in DRIVE_T_BONES:
        if n in ts2:
            T = np.asarray(ts2[n], dtype=np.float64); m = T.mean(0)
            ts2[n] = [tuple(x) for x in (m + (T - m) * g)]
    for n in DRIVE_Q_BONES:
        if n in qs2:
            Q = np.asarray(qs2[n], dtype=np.float64)
            for i in range(1, len(Q)):
                if np.dot(Q[i], Q[0]) < 0:
                    Q[i] = -Q[i]
            m = Q.mean(0); m /= max(np.linalg.norm(m), 1e-12)
            mc = (-m[0], -m[1], -m[2], m[3])
            out = []
            for q in Q:
                d = RV.q_mul(tuple(q), mc)                       # q = d·m ⇒ d = q·m⁻¹
                out.append(tuple(RV.q_norm(RV.q_mul(q_pow(d, g), tuple(m)))))
            qs2[n] = out
    return ts2, qs2, g


# ------------------------------------------------------------------ 场景
def repair_capsule_rotations(model):
    """Recover capsule orientation only when the source geometry determines its axis.

    Fully missing rotations are recoverable for unjointed kinematic capsules:
    rotation about their own axis changes neither collision geometry nor inertia.
    Dynamic/jointed bodies still require their original orientation.
    """
    repaired = []
    for rigid_index, rigid in enumerate(model.rigids):
        angles = np.asarray(rigid.rotation, dtype=float)
        if np.isfinite(angles).all():
            continue
        missing_all = np.isnan(angles).all()
        missing_roll = np.array_equal(np.isfinite(angles), [True, True, False])
        unjointed_wall = rigid.mode == 0 and not any(
            rigid_index in (j.src_rigid, j.dest_rigid) for j in model.joints)
        if rigid.type != 2 or not (missing_roll or (missing_all and unjointed_wall)):
            raise ValueError(f"{rigid.name}: cannot infer invalid rigid rotation {rigid.rotation}")
        if rigid.bone is None or not 0 <= rigid.bone < len(model.bones):
            raise ValueError(f"{rigid.name}: missing bone for capsule axis")
        bone = model.bones[rigid.bone]
        connection = bone.displayConnection
        if isinstance(connection, int) and 0 <= connection < len(model.bones):
            direction = np.asarray(model.bones[connection].location) - np.asarray(bone.location)
        elif isinstance(connection, (tuple, list)) and len(connection) == 3:
            direction = np.asarray(connection, dtype=float)
        else:
            raise ValueError(f"{rigid.name}: missing bone axis for capsule rotation")
        length = np.linalg.norm(direction)
        if not np.isfinite(length) or length < 1e-6:
            raise ValueError(f"{rigid.name}: invalid bone axis")
        direction /= length
        if missing_all:
            offset = np.asarray(rigid.location) - np.asarray(bone.location)
            offset_length = np.linalg.norm(offset)
            if (not np.isfinite(offset_length) or offset_length < 1e-6
                    or np.linalg.norm(np.cross(offset / offset_length, direction)) > 1e-4):
                raise ValueError(f"{rigid.name}: capsule center does not confirm bone axis")
            axis_direction = direction if direction[1] >= 0 else -direction
            angles = np.array([math.atan2(axis_direction[2], axis_direction[1]), 0.0,
                               math.atan2(-axis_direction[0], np.linalg.norm(axis_direction[1:]))])
        else:
            reference = q_to_m(euler_yxz_q((angles[0], angles[1], 0.0)))
            local = reference.T @ direction
            if abs(local[2]) > 1e-4:
                raise ValueError(f"{rigid.name}: finite capsule angles disagree with bone axis")
            candidates = [math.atan2(-sign * local[0], sign * local[1]) for sign in (-1, 1)]
            angles[2] = min(candidates, key=abs)
        axis = q_to_m(euler_yxz_q(angles))[:, 1]
        if abs(np.dot(axis, direction)) < 1.0 - 1e-6:
            raise ValueError(f"{rigid.name}: inferred capsule rotation failed axis validation")
        rigid.rotation = tuple(float(value) for value in angles)
        repaired.append(dict(name=rigid.name, rotation=list(rigid.rotation),
                             source='capsule_bone_and_center_axis' if missing_all else 'capsule_bone_axis'))
    return repaired


def regularize_degenerate_shapes(model, epsilon):
    """Give explicitly opted-in zero-width source geometry a finite thickness.

    PhysX/Bullet input validation requires a solid shape. This only expands zero
    dimensions; positive dimensions, mass, masks, and joints are unchanged.
    The epsilon is recorded in the character registry, in PMX units.
    """
    if not epsilon:
        return []
    if not math.isfinite(epsilon) or not 0 < epsilon <= 0.001:
        raise ValueError('Degenerate shape extent must be in (0, .001] PMX')
    repairs = []
    for rigid in model.rigids:
        dimensions = (0, 1, 2) if rigid.type == 1 else (0,)
        before = tuple(rigid.size)
        if any(not math.isfinite(v) or v < 0 for v in before):
            raise ValueError(f'{rigid.name}: invalid shape dimensions {before}')
        size = list(before)
        for axis in dimensions:
            if size[axis] == 0:
                size[axis] = epsilon
        if tuple(size) != before:
            rigid.size = tuple(size)
            repairs.append(dict(name=rigid.name, source='explicit_degenerate_shape_extent',
                                original_size=list(before), size=size, epsilon_pmx=epsilon))
    return repairs


def regularize_joint_ranges(model):
    """Bring source joints into the PhysX-accepted range, in place on the model.

    PhysX rejects swing limits at exactly ±π (鸣潮 wfl Belt_H_*) and negative
    spring stiffness (wcr Dress*, -0.3 on a ±10° axis). Swing bounds move 1e-4 rad
    inside; negative stiffness becomes no spring. Applied to the model rather than
    the serialized scene so downstream contracts see the same values. None of the
    92 released roles has either case, so their scenes are unchanged (2026-10-09).
    """
    repairs = []
    edge = math.pi - 1e-4
    for j in model.joints:
        lo, hi = list(j.minimum_rotation), list(j.maximum_rotation)
        for ax in (1, 2):
            if lo[ax] < hi[ax]:
                lo[ax] = max(lo[ax], -edge); hi[ax] = min(hi[ax], edge)
        spr_l = [max(x, 0.0) for x in j.spring_constant]
        spr_a = [max(x, 0.0) for x in j.spring_rotation_constant]
        if (lo, hi, spr_l, spr_a) != (list(j.minimum_rotation), list(j.maximum_rotation),
                                      list(j.spring_constant), list(j.spring_rotation_constant)):
            repairs.append(dict(name=j.name, source='physx_joint_range',
                                rotation=[list(j.minimum_rotation), list(j.maximum_rotation)],
                                spring=[list(j.spring_constant), list(j.spring_rotation_constant)]))
            j.minimum_rotation, j.maximum_rotation = lo, hi
            j.spring_constant, j.spring_rotation_constant = spr_l, spr_a
    return repairs


class Rig:
    """一个角色的静态数据：PMX + 骨名映射 + mesh 绑定 + 蒙皮归属。装一次，烘全部动画。"""

    def __init__(self, cfg):
        # Keep the caller's explicit experiment settings independent of the
        # checked-in release recipe. Candidate-source APIs opt out completely.
        cfg = dict(cfg)
        self.release_recipe = None
        if cfg.get('release_recipe', True):
            from physics_release_recipe import load, source_profile
            self.release_recipe = load(cfg['ck'])
            if cfg.get('profile') is None:
                cfg['profile'] = source_profile(cfg['ck'], recipe=self.release_recipe)
        self.cfg = cfg
        pmx = load_pmx_module()
        self.model = pmx.load(str(cfg["pmx"]))
        self.source_repairs = repair_capsule_rotations(self.model)
        for repair in self.source_repairs:
            log(f"[rig] recovered {repair['name']} capsule rotation from bone axis: {repair['rotation']}")
        shape_repairs = regularize_degenerate_shapes(
            self.model, X.BY_CK[cfg['ck']].get('phys_degenerate_extent', 0))
        for repair in shape_repairs:
            log(f"[rig] finite source geometry {repair['name']}: {repair['original_size']} -> {repair['size']} PMX")
        self.source_repairs.extend(shape_repairs)
        joint_repairs = regularize_joint_ranges(self.model)
        if joint_repairs:
            log(f"[rig] PhysX joint range: {len(joint_repairs)} joints (±π swing / negative spring)")
        self.source_repairs.extend(joint_repairs)
        self.B = self.model.bones
        prefixes = tuple(name.lower() for name in X.BY_CK[cfg['ck']].get('phys_follow_bone', ()))
        # 试验根按角色追加跟随前缀（2026-10-12 菲比蓝带子：删掉刚体、跟骨）。缺省空 = 旧行为。
        prefixes += tuple(name.lower() for name in cfg.get('follow_prefixes', ()))
        if cfg.get('simulate_follow_bones'):
            log(f"[rig] isolated trial: restoring source physics for follow-bone prefixes {prefixes}")
            prefixes = ()
        self.follow_rigids = {index for index, rigid in enumerate(self.model.rigids)
                              if prefixes and rigid.name.lower().startswith(prefixes)}
        if cfg.get('profile'):
            from physics_profile import apply_rigids, relax_skirt_row0
            apply_rigids(self.model, cfg['profile'], self.follow_rigids)
            # 裙子行 0 的横向关节原版是焊死的（旋转/位移/弹簧全零），构成闭环网格里
            # 最紧的一圈，是抖动的拓扑来源。默认 0.0 不动，显式给值才放开。
            # 必须排在 apply_rigids 之后（否则 limit_scale 会覆盖）、follow 收尾之前
            # （那一步会删掉 follow 刚体上的关节，删了就改不到）。
            row0 = float(cfg['profile'].get('solver', {}).get('skirt_row0_deg', 0.0))
            if row0 > 0.0:
                # 打印计数防静默失效：匹配靠 PMX 的 スカート_<行>_<列> 命名，
                # 命名不符时返回 0 而不报错。
                n_relaxed = relax_skirt_row0(self.model, row0)
                log(f"[rig] {cfg['ck']}: 裙子行 0 横向关节放开 {row0}° —— 改动 {n_relaxed} 个")
                if not n_relaxed:
                    log(f"[rig] !! skirt_row0_deg={row0} 但一个关节都没匹配上 "
                        f"—— 检查 PMX 是否用 スカート_<行>_<列> 命名")
        for index in self.follow_rigids:
            self.model.rigids[index].mode = 0
            self.model.rigids[index].collision_group_mask = 0
        self.model.joints = [joint for joint in self.model.joints
                             if joint.src_rigid not in self.follow_rigids
                             and joint.dest_rigid not in self.follow_rigids]
        if self.follow_rigids:
            log(f"[rig] {cfg['ck']}: {len(self.follow_rigids)} accessory/ribbon rigids follow animation bones")
        name_map, _ = MC.build_name_map([b.name for b in self.B])
        self.pmx_to_ours = name_map                       # 日文名 → bn_m_*
        self.ours_to_pmx_idx = {name_map[b.name]: i for i, b in enumerate(self.B)}
        self.torso_path = MP.find(cfg["torso"])
        self.head_path = MP.find(cfg["head"])
        self.parents = RV.mesh_parents(self.torso_path)
        self.bind = mesh_bind(self.torso_path)
        self.bind.update({k: v for k, v in mesh_bind(self.head_path).items() if k not in self.bind})
        BPI.HEAD_MESH, BPI.TORSO_MESH = cfg["head"], cfg["torso"]
        BPI.BODY_PREFIX, BPI.HEAD_PREFIX = cfg["body_prefix"], cfg["head_prefix"]
        self.owner = BPI.skin_owner_map((cfg["head"], cfg["torso"]))
        # 刚体 → 我们的骨名（在 mesh 里才写回）
        self.rigid_bone = []
        for r in self.model.rigids:
            jp = self.B[r.bone].name if (r.bone is not None and r.bone >= 0) else None
            ours = name_map.get(jp) if jp else None
            self.rigid_bone.append(ours if ours in self.parents else None)
        n_absent = sum(1 for r, o in zip(self.model.rigids, self.rigid_bone)
                       if r.bone is not None and r.bone >= 0 and o is None)
        log(f"[rig] {cfg['ck']}: PMX 骨 {len(self.B)} 刚体 {len(self.model.rigids)} 关节 "
            f"{len(self.model.joints)}；刚体骨不在 mesh 里的 {n_absent}（錘/零蒙皮，参与解算不写回）"
            f"  scale {cfg['scale']}")
        # 手臂墙（腕/手/ひじ 的模式 0 刚体）：--arm-shield 按动画屏蔽它们对长期嵌入的挂件的碰撞
        self.arm_idx = [i for i, r in enumerate(self.model.rigids)
                        if r.mode == 0 and r.bone is not None and r.bone >= 0
                        and any(k in self.B[r.bone].name for k in ("腕", "手", "ひじ"))]

    def arm_overlap(self, frames, F, thresh):
        """FK 姿态（不解算）下，与任一手臂墙球心距 < 半径和的帧占比 ≥ thresh 的动态刚体索引集。
        判据来自 work/evidence/phys_ab/_overlap.py：洛茜/艾尔黛拉 idle 里 16/20 个裙刚体被手臂墙
        压 100% 帧，dungeon 里 2~4 个发刚体被扎 85~100% 帧 —— 每子步「被推出 vs 被关节拉回」
        打架就是高频抖动与拉伸的来源；一扫而过（stayback/dance ≤ 38%）不屏蔽。"""
        R, B = self.model.rigids, self.B
        fs = list(range(0, F, max(1, F // 24)))

        def center(f, i):
            r = R[i]; q = frames[f, r.bone, 3:]; x, y, z, w = q
            M = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            return frames[f, r.bone, :3] + M @ (np.asarray(r.location) - np.asarray(B[r.bone].location))
        rad = lambda r: max(r.size[:2]) if r.type == 1 else r.size[0]
        arms = [(a, rad(R[a]), [center(f, a) for f in fs]) for a in self.arm_idx]
        out = set()
        touched = set()   # 碰过手臂但没到阈值的格子，供连通域扩散用
        for i, r in enumerate(R):
            if r.mode == 0 or r.bone is None or r.bone < 0:
                continue
            # 只屏蔽头发与裙子（用户的两处症状）。斗篷/挂件长期贴手臂是**搭在**上面而不是被
            # 扎穿，撤掉手臂它就整个掉下来：洛茜 idle 斗篷分离率 5% → 44%、接缝 1.9° → 23.5°
            # （矩阵 shield05，2026-09-06）。
            ours = self.rigid_bone[i] or ""
            if not any(k in ours.lower() for k in ("hair", "skirt")):
                continue
            ri = rad(r); hit = 0
            for k, f in enumerate(fs):
                ci = center(f, i)
                if any(np.linalg.norm(ci - ac[k]) < ri + ar for _a, ar, ac in arms):
                    hit += 1
            if hit / len(fs) >= thresh:
                out.add(i)
            elif hit > 0:
                touched.add(i)      # 碰过但没过阈值：连通域扩散的候选
        # 连通域扩散：与已屏蔽格由关节直连、且自身也碰过手臂的格子一并屏蔽。
        # 逐格阈值会在空间上切出锯齿边界（绛雨 idle 的 7 个边界格全部与屏蔽格直连），
        # 一格被推、邻格不被推，关节把两者拉着打架 = 手臂侵入处的抽动。
        # 只扩散到 touched（真碰过）的格子：完全不碰手臂的邻格不动，否则整片裙子
        # 都免碰会让它穿过手臂（斗篷那次的教训，见 physics-arm-shield-mask-semantics）。
        if touched and out:
            adjacent = {}
            for joint in self.model.joints:
                a, b = joint.src_rigid, joint.dest_rigid
                adjacent.setdefault(a, set()).add(b)
                adjacent.setdefault(b, set()).add(a)
            n_spread = 0
            while True:
                grew = {i for i in touched
                        if i not in out and (adjacent.get(i, ()) & out)}
                if not grew:
                    break
                out |= grew
                n_spread += len(grew)
            if n_spread:
                log(f"[rig] 手臂屏蔽连通域扩散 +{n_spread} 格"
                    f"（阈值内 {len(out) - n_spread}，边界候选 {len(touched)}）")
        return out

    # ---------------- rest 穿透 → 墙半径收缩（2026-09-09，替代整体免碰，坐姿仍有腿撑裙）
    @staticmethod
    def _rot_yxz(e):
        x, y, z = e
        cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
        Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]); Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
        return Ry @ Rx @ Rz

    @staticmethod
    def _sdf(shape, q):
        lq = shape["rot"].T @ (q - shape["pos"]); sz = shape["size"]
        if shape["typ"] == 0:
            return float(np.linalg.norm(lq) - sz[0])
        if shape["typ"] == 2:
            h = sz[1] / 2; c = np.array([0.0, float(np.clip(lq[1], -h, h)), 0.0]); return float(np.linalg.norm(lq - c) - sz[0])
        a = np.abs(lq) - sz
        return float(np.linalg.norm(np.maximum(a, 0)) + min(max(a[0], a[1], a[2]), 0))

    @staticmethod
    def _samples(shape):
        sz = shape["size"]
        if shape["typ"] == 0:
            return [shape["pos"]], sz[0]
        if shape["typ"] == 2:
            h = sz[1] / 2
            return [shape["pos"] + shape["rot"] @ np.array([0.0, t, 0.0]) for t in np.linspace(-h, h, 9)], sz[0]
        pts = [np.array([a * sz[0], b * sz[1], c * sz[2]]) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
        return [shape["pos"] + shape["rot"] @ q for q in pts], 0.0

    def wall_fit(self, groups, margin=0.02, min_scale=0.5):
        """返回 {墙刚体下标: 新 size}。对 groups 里的球/胶囊墙，按可碰撞动态刚体的最大 rest 穿透量收缩半径。"""
        if getattr(self, "_wall_fit_cache", None) is not None:
            return self._wall_fit_cache
        R = self.model.rigids
        sh = []
        for r in R:
            sh.append(dict(typ=r.type, size=np.array(r.size, float), pos=np.array(r.location, float),
                           rot=self._rot_yxz(r.rotation), mode=r.mode, grp=r.collision_group_number,
                           msk=r.collision_group_mask & 0xFFFF))
        def can(a, b): return ((a["msk"] >> b["grp"]) & 1) and ((b["msk"] >> a["grp"]) & 1)
        out = {}
        for wi, w in enumerate(sh):
            if w["mode"] != 0 or w["grp"] not in groups or w["typ"] not in (0, 2):
                continue
            pen_max = 0.0
            for d in sh:
                if d["mode"] == 0 or not can(d, w):
                    continue
                if np.linalg.norm(d["pos"] - w["pos"]) > np.linalg.norm(d["size"]) + np.linalg.norm(w["size"]) + w["size"][0]:
                    continue
                pa, ra = self._samples(d); pen = -(min(self._sdf(w, q) for q in pa) - ra)
                pb, rb = self._samples(w); pen = max(pen, -(min(self._sdf(d, q) for q in pb) - rb))
                pen_max = max(pen_max, pen)
            if pen_max <= 0:
                continue
            r0 = float(w["size"][0]); r1 = max(r0 - pen_max - margin, r0 * min_scale)
            size = [float(x) for x in R[wi].size]; size[0] = r1
            out[wi] = (size, r0, pen_max)
        log(f"[rig] wall-fit 组 {sorted(groups)}：收缩 {len(out)} 面墙 "
            + "；".join(f"{R[i].name} r{r0:.2f}→{sz[0]:.2f}(嵌{p:.2f})" for i, (sz, r0, p) in list(out.items())[:8])
            + (" …" if len(out) > 8 else ""))
        self._wall_fit_cache = {i: sz for i, (sz, _, _) in out.items()}
        return self._wall_fit_cache

    def _scene_text(self, ang_cap=None, shield=None, erp_skip=("skirt",), nocol=(), wfit=None):
        """场景文本。ang_cap：动态刚体角阻尼上限（Bullet 把 ≥1 夹成 1 → 每子步角速度清零，洛茜裙
        全部 1.62~2.0、艾尔黛拉裙 0.99，链没有输入就死）。shield：要对手臂墙免碰的刚体索引集 ——
        手臂墙挪进一个空闲 group，原来与它们相碰的动态刚体补上该位，shield 里的清掉该位。"""
        R = self.model.rigids
        free_g = None
        if shield:
            used = {r.collision_group_number for r in R}
            free_g = next((g for g in range(16) if g not in used), None)
            if free_g is None:
                log("  !! 16 个碰撞组全被占用，手臂屏蔽无法实施"); shield = None
        arm_groups = {R[a].collision_group_number for a in self.arm_idx}
        n_cap = n_sh = 0
        n_chain_nocol = n_chain_wall = 0
        L = ["# 由 moe_bullet_bake.py 生成，PMX 原生空间", "fps 30", f"nbones {len(self.B)}"]
        for b in self.B:
            p = b.parent if (b.parent is not None and b.parent >= 0) else -1
            L.append(f"bone {p} {b.location[0]:.7g} {b.location[1]:.7g} {b.location[2]:.7g}")
        L.append(f"nrigids {len(self.model.rigids)}")
        # 「球径 / 枢轴间距」上限（阶段二第 2 项，替代旧 DIM_PATCH）。洛茜尾巴球半径 0.7 配骨距
        # 0.36~0.49：dance_spin 里腿墙扫过尾根第 4 节，被推开半个骨距，jnt3 留在原位 → 46% 拉伸
        # （60 次迭代 / 480 Hz 都压不掉，碰撞几何本身就是这么大）。cap=None 是纯 saba 基线。
        cap = self.cfg.get("ball_cap")
        n_shrunk = 0
        for i, r in enumerate(R):
            bi = r.bone if (r.bone is not None and r.bone >= 0) else -1
            v = lambda a: " ".join(f"{x:.7g}" for x in a)
            size = list(r.size)
            if wfit and i in wfit:
                size = list(wfit[i])
            grp, msk, angd = r.collision_group_number, r.collision_group_mask & 0xFFFF, r.rotation_attenuation
            if ang_cap is not None and r.mode != 0 and angd > ang_cap:
                angd = ang_cap; n_cap += 1
            for group_a, group_b in nocol:
                if grp == group_a:
                    msk &= ~(1 << group_b) & 0xFFFF
                if grp == group_b:
                    msk &= ~(1 << group_a) & 0xFFFF
            rigid_name = r.name.lower()
            # 不筛 mode：挂件自带的碰撞板（如绛雨 cloth_loft 的 5 面墙，掩码 0xff6d
            # 与同组头发 0xffef 本就不同）也要能免碰，否则登记了只改到动态体、
            # 墙照旧碰撞 = 配置静默不生效。安全性靠前缀本身：登记的是挂件名，
            # 身体墙叫 下半身A/左ひじ，前缀撞不上。
            for prefixes, blocked_groups in X.BY_CK[self.cfg['ck']].get('phys_chain_nocollide', ()):
                if rigid_name.startswith(tuple(prefix.lower() for prefix in prefixes)):
                    for blocked_group in blocked_groups:
                        msk &= ~(1 << blocked_group) & 0xFFFF
                    n_chain_nocol += 1
                    if r.mode == 0:
                        n_chain_wall += 1
            if shield:
                if i in self.arm_idx:
                    grp = free_g
                elif r.mode != 0:
                    # 空闲位 = 原来对手臂组的位（PMX 未列出的组默认 1，不能只补不清）
                    hit_arm = any((msk >> g) & 1 for g in arm_groups)
                    msk &= ~(1 << free_g) & 0xFFFF
                    if hit_arm and i not in shield:
                        msk |= 1 << free_g
                    if hit_arm and i in shield:
                        n_sh += 1
            if cap and r.mode != 0 and r.type == 0 and bi >= 0:
                pb = self.B[bi]
                if pb.parent is not None and pb.parent >= 0:
                    spacing = math.dist(pb.location, self.B[pb.parent].location)
                    if spacing > 1e-4 and size[0] > cap * spacing:
                        size[0] = cap * spacing
                        n_shrunk += 1
            L.append(f"rigid {bi} {r.type} {v(size)} {v(r.location)} {v(r.rotation)} "
                     f"{r.mass:.7g} {r.velocity_attenuation:.7g} {angd:.7g} "
                     f"{r.bounce:.7g} {r.friction:.7g} {r.mode} {grp} {msk}")
        L.append(f"njoints {len(self.model.joints)}")
        # 第 28 列 soft：该关节是否参与 --erp-limit 软化。按子刚体（动态的那端）所属组定；erp_skip 里的组写 0。
        def _grp_of_rigid(ri):
            if ri is None or ri < 0:
                return "other"
            r = R[ri]
            bi = r.bone if (r.bone is not None and r.bone >= 0) else -1
            if bi < 0:
                return "other"
            return (self.pmx_to_ours.get(self.B[bi].name) or self.B[bi].name).lower()
        # erp_skip 是骨名子串（GROUP_KEYS 本就是子串，所以 "skirt" 仍按组生效；洁尔佩塔的飘带是 qb/qj）
        n_skip = 0
        for j in self.model.joints:
            a = j.src_rigid if j.src_rigid is not None else -1
            b = j.dest_rigid if j.dest_rigid is not None else -1
            child = b if (b >= 0 and R[b].mode != 0) else a
            _nm = _grp_of_rigid(child)
            soft = 0 if any(k in _nm for k in (erp_skip or ())) else 1
            n_skip += soft == 0
            v = lambda a_: " ".join(f"{x:.7g}" for x in a_)
            L.append(f"joint {a} {b} {v(j.location)} {v(j.rotation)} {v(j.minimum_location)} "
                     f"{v(j.maximum_location)} {v(j.minimum_rotation)} {v(j.maximum_rotation)} "
                     f"{v(j.spring_constant)} {v(j.spring_rotation_constant)} {soft}")
        if erp_skip:
            log(f"[rig] erp-limit 不软化的关节（组 {','.join(erp_skip)}）：{n_skip}/{len(self.model.joints)}")
        if cap:
            log(f"[rig] 球径上限 {cap}×枢轴间距：缩了 {n_shrunk} 个动态球体")
        if nocol:
            log(f"[rig] 组对免碰 {list(nocol)}（登记表 phys_nocollide 或 --no-collide-groups）")
        return "\n".join(L) + "\n", {"ang_capped": n_cap, "arm_shielded": n_sh,
                                  "chain_nocol": n_chain_nocol,
                                  "chain_nocol_wall": n_chain_wall}

    # ---- 每条动画
    def bone_frames_pmx(self, F, W):
        """全部 PMX 骨的逐帧世界变换（PMX 空间）→ (F, NB, 7)。
        在 mesh 里的骨：[R_f R_bind⁻¹ | p_f / scale]；不在的：按 PMX rest 局部偏移跟父骨。"""
        sc = self.cfg["scale"]
        NB = len(self.B)
        out = np.zeros((F, NB, 7), dtype=np.float32)
        Rw = [None] * NB
        pw = [None] * NB
        # **PMX 不保证父索引 < 子索引**（transform_order 就是为此存在的），按拓扑序求。
        seq, seen = [], set()

        def visit(i):
            if i in seen or i < 0:
                return
            seen.add(i)
            bi = self.B[i]
            visit(bi.parent if (bi.parent is not None and bi.parent >= 0) else -1)
            seq.append(i)
        for i in range(NB):
            visit(i)
        for i in seq:
            b = self.B[i]
            ours = self.pmx_to_ours.get(b.name)
            if ours in W and ours in self.bind:
                R, p = W[ours]
                Rb = self.bind[ours][0]
                Rw[i] = R @ Rb.T
                pw[i] = p / sc
            else:
                pi = b.parent if (b.parent is not None and b.parent >= 0) else -1
                if pi >= 0 and Rw[pi] is not None:
                    off = np.asarray(b.location) - np.asarray(self.B[pi].location)
                    Rw[i] = Rw[pi]
                    pw[i] = pw[pi] + np.einsum("fij,j->fi", Rw[pi], off)
                else:
                    Rw[i] = np.repeat(np.eye(3)[None], F, axis=0)
                    pw[i] = np.repeat(np.asarray(b.location, dtype=np.float64)[None], F, axis=0)
            out[:, i, :3] = pw[i]
            out[:, i, 3:] = ms_to_qs(Rw[i])
        return out

    def prepare_one(self, anim_path, a):
        """Prepare the shared input once; both single and GPU batch solves use it."""
        t0 = time.time()
        source_path = Path(anim_path).resolve()
        if source_path.is_relative_to((ROOT / "mod").resolve()):
            raise ValueError("Physics input must be an unsimulated work asset, not a shipped model animation")
        if source_path.is_relative_to((ROOT / "work" / "physics_release").resolve()):
            raise ValueError("Physics release outputs cannot be used as unsimulated inputs")
        base = source_path.stem
        cfg = self.cfg
        if base.startswith("moe_body_"):
            base = cfg["body_prefix"] + base[len("moe_body_"):]
        if not base.startswith(cfg["body_prefix"]):
            raise ValueError(f"Physics input belongs to another character or part: {source_path.name}")
        meta, bones, samp = RV.load_anim(anim_path)
        F, ts, qs = RV.unpack_samples(meta, bones, samp)
        rest = {b["name"]: b for b in bones}
        order = topo_order(list(rest), self.parents)
        # 驱动放大（用户 2026-09-06「动态不要风场」）：解算与物理骨的父骨参考都用放大后的躯干运动，
        # 写回身体通道仍用 ts/qs 原样（见 amplify_drive）。
        ts_d, qs_d, g_used = amplify_drive(ts, qs, rest, F, a.drive_gain, a.drive_thresh)
        W = fk_frames(F, rest, ts_d, qs_d, self.parents, order)
        frames = self.bone_frames_pmx(F, W)

        # 中间文件（scene/frames/out.bin，每条约 5 MB）固定写 work/，**不跟 out_dir 走**：
        # 2026-09-06 全量把 --out-dir 直接指到物理包 anims/ 时，1.8 GB 的 _io 跟着落进 mod 目录，
        # finalize_mods 会原样同步进游戏。
        backend = getattr(a, 'backend', 'bullet')
        work = Path(a.io_dir) if getattr(a, 'io_dir', None) else ROOT / "work" / ("bullet_native" if backend == 'bullet' else backend) / cfg["ck"] / "_io"
        work.mkdir(parents=True, exist_ok=True)
        sp, fp, op = work / f"{base}.scene.txt", work / f"{base}.frames.bin", work / f"{base}.out.bin"
        shield = self.arm_overlap(frames, F, a.arm_shield) if (a.arm_shield > 0 and self.arm_idx) else None
        # 角阻尼上限：命令行优先，否则读登记表（绛雨坐姿定的 0.9）
        ang_cap = a.ang_damp_cap if a.ang_damp_cap is not None \
            else X.BY_CK[cfg['ck']].get('phys_ang_damp_cap')
        scene_txt, sc_info = self._scene_text(ang_cap=ang_cap, shield=shield,
                                              erp_skip=(tuple(x for x in a.erp_limit_skip.split(",") if x)
                                                        if a.erp_limit_skip is not None
                                                        else tuple(X.BY_CK[a.ck].get("phys_erp_skip") or ())),
                                              nocol=(tuple((int(x.split(":")[0]), int(x.split(":")[1]))
                                                           for x in a.no_collide_groups.split(",") if x)
                                                     if a.no_collide_groups is not None
                                                     else tuple(tuple(q) for q in (X.BY_CK[a.ck].get("phys_nocollide") or ()))),
                                              wfit=(self.wall_fit(set(int(x) for x in a.wall_fit.split(",") if x), a.wall_fit_margin, a.wall_fit_min)
                                                    if a.wall_fit is not None
                                                    else (self.wall_fit(set(X.BY_CK[a.ck]["phys_wall_fit"]), a.wall_fit_margin, a.wall_fit_min)
                                                          if X.BY_CK[a.ck].get("phys_wall_fit") else None)))
        sp.write_text(scene_txt, encoding="utf-8")
        with open(fp, "wb") as f:
            f.write(struct.pack("<ii", F, len(self.B)))
            f.write(frames.tobytes())
        loop = is_looping(base)   # 键是完整 animation id = 文件名 stem，不能剥前缀
        return dict(t0=t0, base=base, meta=meta, bones=bones, F=F, ts=ts, qs=qs,
                    rest=rest, W=W, g_used=g_used, work=work, sp=sp, fp=fp, op=op,
                    loop=loop, sc_info=sc_info, source_dir=str(source_path.parent))

    def solver_command(self, prepared, a):
        cfg = self.cfg
        work, sp, fp, op = (prepared[k] for k in ('work', 'sp', 'fp', 'op'))
        base, loop = prepared['base'], prepared['loop']
        # warmup 与 erp2：命令行/profile 优先，否则读登记表（绛雨坐姿定的 3 / 0.02）
        reg = X.BY_CK[cfg["ck"]]
        warmup = a.warmup if a.warmup != 1 else (reg.get("phys_warmup") or 1)
        if getattr(a, 'backend', 'bullet') != 'bullet':
            cmd = [str(a.exe), str(sp), str(fp), str(op), '--device', a.backend.split('-')[1],
                   '--allow-angular-approximation', '--warmup', str(warmup), '--settle', str(a.settle),
                   '--intro', str(a.intro), '--intro-hold', str(a.intro_hold), '--loop-extra', str(a.loop_tail),
                   '--hz', str(a.hz), '--iters', str(a.physx_iters), '--velocity-iters', str(a.physx_velocity_iters),
                   '--threads', str(a.physx_threads), '--contact-offset', str(a.physx_contact_offset),
                   '--drive-damping', str(a.physx_drive_damping),
                   '--max-depenetration-velocity', str(a.physx_max_depenetration_velocity)]
            if getattr(a, 'physx_depth_depenetration', None):
                # [lo depth, hi depth, velocity at lo]; needs a solver built after 09-28.
                lo, hi, vlo = a.physx_depth_depenetration
                cmd += ['--depth-depenetration', str(lo), str(hi), str(vlo)]
            if getattr(a, 'physx_record_depenetration_velocity', 0):
                # Needs a solver built after 09-27; older exes reject the flag.
                cmd += ['--record-depenetration-velocity', str(a.physx_record_depenetration_velocity)]
            if getattr(a, 'physx_angular_limit_hz', 0):
                cmd += ['--angular-limit-hz', str(a.physx_angular_limit_hz)]
            if getattr(a, 'physx_disable_preprocessing', False):
                cmd.append('--disable-preprocessing')
            if getattr(a, 'physx_contact_frequency_hz', 0):
                cmd += ['--contact-frequency-hz', str(a.physx_contact_frequency_hz),
                        '--contact-damping-ratio', str(getattr(a, 'physx_contact_damping_ratio', 1))]
            if getattr(a, 'physx_contact_rigids', None):
                cmd += ['--contact-rigids', str(a.physx_contact_rigids)]
            if getattr(a, 'physx_solver', 'tgs') != 'tgs':
                cmd += ['--solver', a.physx_solver]
            if getattr(a, 'physx_angular_limit_rigids', None):
                cmd += ['--angular-limit-rigids', str(a.physx_angular_limit_rigids)]
            if getattr(a, 'physx_external_forces_every_iteration', False):
                cmd.append('--external-forces-every-iteration')
            if not getattr(a, 'physx_bone_twist_projection', True):
                cmd.append('--disable-bone-twist-projection')
            if a.wind:
                cmd += ['--wind', str(a.wind), '--wind-period', str(a.wind_period),
                        '--wind-turb', str(a.wind_turb), '--wind-base', str(a.wind_base),
                        '--wind-dir', *[str(x) for x in a.wind_dir]]
                if a.wind_accel:
                    cmd.append('--wind-accel')
            if loop:
                cmd.append('--loop')
            if a.gravity is not None:
                cmd += ['--gravity', str(a.gravity)]
            if a.contact_report:
                cmd += ['--contact-report', str(work / f'{base}.contacts.csv')]
            return cmd
        cmd = [str(a.exe), str(sp), str(fp), str(op), "--warmup", str(warmup),
               "--settle", str(a.settle), "--intro", str(a.intro),
               "--intro-hold", str(a.intro_hold)]
        if loop:
            cmd.append("--loop")
            if a.loop_tail > 0:
                cmd += ["--loop-extra", str(min(a.loop_tail, prepared['F'] // 2) if prepared['F'] >= 4 else 0)]
        if a.gravity is not None:
            cmd += ["--gravity", str(a.gravity)]
        if a.iters != 10:
            cmd += ["--iters", str(a.iters)]
        if a.hz != 120:
            cmd += ["--hz", str(a.hz)]
        # erp2 的 CLI 默认是 0.1（不是 None），所以「等于默认」才让登记表接手
        erp2 = a.erp2 if a.erp2 != 0.1 else (reg.get("phys_erp2") if reg.get("phys_erp2") is not None else 0.1)
        if erp2 is not None:
            cmd += ["--erp2", str(erp2)]
        if a.erp is not None:
            cmd += ["--erp", str(a.erp)]
        if a.split_impulse_threshold is not None:
            cmd += ["--split-impulse-threshold", str(a.split_impulse_threshold)]
        if a.linear_slop is not None and a.linear_slop >= 0:
            cmd += ["--linear-slop", str(a.linear_slop)]
        if a.intro_deflate is not None and a.intro_deflate < 1.0:
            cmd += ["--intro-deflate", str(a.intro_deflate)]
        if a.contact_stiffness is not None:
            cmd += ["--contact-stiffness", str(a.contact_stiffness)]
            if a.contact_damping is not None:
                cmd += ["--contact-damping", str(a.contact_damping)]
        if a.erp_limit is not None and a.erp_limit >= 0:
            cmd += ["--erp-limit", str(a.erp_limit)]
            cmd += ["--erp-limit-min-range", str(a.erp_limit_min_range)]
        if a.spring2:
            cmd.append("--spring2")
        if a.contact_report:
            # 每对刚体一行 rigid_a,rigid_b,frames,max_depth，写在中间件旁边
            cmd += ["--contact-report", str(work / f"{base}.contacts.csv")]
        if a.wind:
            cmd += ["--wind", str(a.wind), "--wind-period", str(a.wind_period), "--wind-turb", str(a.wind_turb),
                    "--wind-base", str(a.wind_base), "--wind-dir", *[str(x) for x in a.wind_dir]]
            if a.wind_accel:
                cmd.append("--wind-accel")
        return cmd

    @staticmethod
    def run_solver(cmd, prepared):
        with Path(cmd[0]).open('rb') as executable:
            executable_hash = hashlib.file_digest(executable, 'sha256').hexdigest()
        started = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        elapsed = time.time() - started
        for item in prepared:
            Path(str(item['op']) + '.command.json').write_text(json.dumps(
                dict(command=cmd, solver_sha256=executable_hash, elapsed_seconds=elapsed, returncode=proc.returncode,
                     stdout=proc.stdout, stderr=proc.stderr), ensure_ascii=False, indent=2), encoding='utf-8')
        if proc.returncode != 0:
            raise RuntimeError(f"solver exited {proc.returncode}\n{proc.stderr.strip()}")
        return proc

    def bake_one(self, anim_path, out_dir, a):
        if self.release_recipe is not None:
            from physics_release_recipe import options_for_action
            a = options_for_action(self.cfg['ck'], self._recipe_action(anim_path), a,
                                   recipe=self.release_recipe)
        prepared = self.prepare_one(anim_path, a)
        proc = self.run_solver(self.solver_command(prepared, a), [prepared])
        return self.finish_one(prepared, out_dir, a, proc)

    def bake_batch(self, anim_paths, out_dir, a):
        """Independent timelines share one PhysX scene; body/head writeback stays common."""
        if self.release_recipe is None:
            return self._bake_uniform_batch(anim_paths, out_dir, a)
        from physics_release_recipe import partition_actions
        groups = partition_actions(self.cfg['ck'],
                                   [(self._recipe_action(p), p) for p in anim_paths], a,
                                   recipe=self.release_recipe)
        results, failures = {}, []
        for effective, paths in groups:
            group_results, group_failures = self._bake_uniform_batch(paths, out_dir, effective)
            if results.keys() & group_results.keys():
                raise ValueError('Duplicate body action across release recipe batches')
            results.update(group_results)
            failures.extend(group_failures)
        return results, failures

    def _recipe_action(self, anim_path):
        stem = Path(anim_path).stem
        prefix = self.cfg['body_prefix']
        if not stem.startswith(prefix):
            raise ValueError(f'Release recipe expected character body prefix {prefix}: {stem}')
        return stem[len(prefix):]

    def _bake_uniform_batch(self, anim_paths, out_dir, a):
        """All actions in this native invocation have identical solver options."""
        prepared = [self.prepare_one(p, a) for p in anim_paths]
        manifest = prepared[0]['work'] / f"physx_batch_{prepared[0]['base']}.tsv"
        rows = []
        for item in prepared:
            fields = [str(item[k].resolve()) for k in ('sp', 'fp', 'op')] + [str(int(item['loop']))]
            if any('\t' in value or '\n' in value for value in fields):
                raise ValueError('batch input paths cannot contain tabs or newlines')
            rows.append('\t'.join(fields))
        manifest.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        single = self.solver_command(prepared[0], a)
        cmd = single[:1] + ['--batch', str(manifest)] + [v for v in single[4:] if v != '--loop']
        proc = self.run_solver(cmd, prepared)
        results, failures = {}, []
        for item in prepared:
            try:
                results[item['base']] = self.finish_one(item, out_dir, a, proc)
            except Exception as error:
                failures.append((item['base'], f'{type(error).__name__}: {error}'))
        return results, failures

    def finish_one(self, prepared, out_dir, a, proc):
        cfg = self.cfg
        t0, base, meta, bones, F, ts, qs = (prepared[k] for k in ('t0', 'base', 'meta', 'bones', 'F', 'ts', 'qs'))
        rest, W, g_used, op, loop, sc_info = (prepared[k] for k in ('rest', 'W', 'g_used', 'op', 'loop', 'sc_info'))
        t_sim = time.time() - t0
        with open(op, "rb") as f:
            F2, NR = struct.unpack("<ii", f.read(8))
            rb = np.frombuffer(f.read(), dtype=np.float32).reshape(F2, NR, 7).astype(np.float64)
        K = min(a.loop_tail, F // 2) if (loop and a.loop_tail > 0 and F >= 4) else 0
        assert F2 == F + K and NR == len(self.model.rigids), (F2, F, K, NR)
        if not np.isfinite(rb).all() or not np.allclose(np.linalg.norm(rb[:, :, 3:], axis=2), 1, atol=1e-3):
            raise ValueError('solver returned invalid transforms')
        solver_report = None
        if getattr(a, 'backend', 'bullet') != 'bullet':
            solver_report = json.loads(Path(str(op) + '.solver.json').read_text(encoding='utf-8'))
            # This only rejects catastrophic divergence, not subtle/visual defects.
            model_extent = float(np.linalg.norm(np.ptp(np.array([b.location for b in self.B]), axis=0)))
            if solver_report['max_linear_limit_error_pmx'] > max(model_extent, 1.0):
                error = solver_report['max_linear_limit_error_pmx']
                joint_index = solver_report.get('max_linear_error_joint', -1)
                detail = ''
                if 0 <= joint_index < len(self.model.joints):
                    detail = (f"; joint {joint_index} ({self.model.joints[joint_index].name}), "
                              f"recorded frame {solver_report['max_linear_error_frame']} (zero-based)")
                raise ValueError(f'PhysX constraint separation {error:.3f} PMX exceeds model extent '
                                 f'{max(model_extent, 1.0):.3f} PMX{detail}; '
                                 'raw evidence retained, animation rejected')
        # ---- 循环接缝：交叉淡入（在刚体解上做，之后的骨换算、投影一律照常）
        # 续录段 rb[F+i] 是尾帧的真实物理后继。第 0 帧全取与尾帧相邻的真实采样，
        # 之后 K 帧以端点一、二阶导数为零的权重过渡回本圈。只混合真实解，不制造摆动。
        seam_b4 = seam_af = 0.0
        if K:
            dyn_idx = [i for i, r in enumerate(self.model.rigids) if r.mode != 0]
            for i in dyn_idx:
                seam_b4 = max(seam_b4, q_angle_deg(rb[F - 1, i, 3:], rb[0, i, 3:]))   # 含真实一帧运动与未收敛差异
            ex = rb[F:F + K].copy()
            # slerp 逐帧取最短路径：两条序列相差近 180° 时点积在帧间过零，路径换边，
            # 骨头一帧甩 139~150°（林 laugh/dance_loop 后发，接缝前差 151°）。
            # 先让两条序列各自时间上符号连续，再整体选一个符号，淡入期间不再逐帧翻转。
            for i in dyn_idx:
                for seq in (rb[:K, i, 3:], ex[:, i, 3:]):
                    for k in range(1, K):
                        if float(np.dot(seq[k], seq[k - 1])) < 0:
                            seq[k] = -seq[k]
                if float((rb[:K, i, 3:] * ex[:, i, 3:]).sum()) < 0:
                    ex[:, i, 3:] = -ex[:, i, 3:]
            for k in range(K):
                t = k / K; w = 1.0 - t ** 3 * (10 - 15 * t + 6 * t * t)  # C2 continuation blend
                for i in dyn_idx:
                    rb[k, i, :3] = rb[k, i, :3] * (1 - w) + ex[k, i, :3] * w
                    rb[k, i, 3:] = slerp_path(rb[k, i, 3:], ex[k, i, 3:], w)
            rb = rb[:F]
            for i in dyn_idx:
                seam_af = max(seam_af, q_angle_deg(rb[F - 1, i, 3:], rb[0, i, 3:]))

        # ---- 刚体解 → 骨（PMX 空间）→ 我们空间 → 父骨当帧局部量
        sc = cfg["scale"]
        Rb_ours, pb_ours, mode_of = {}, {}, {}
        for i, r in enumerate(self.model.rigids):
            ours = self.rigid_bone[i]
            if ours is None or r.mode == 0 or ours not in rest:
                continue
            # rbWorld = boneWorld · offset ⇒ boneWorld = rbWorld · offset⁻¹；offset = inv(T(pos)) · rbRest
            Rr = qs_to_ms(rb[:, i, 3:])
            pr = rb[:, i, :3]
            Rrest = q_to_m(euler_yxz_q(r.rotation))
            off_R = Rrest
            off_t = np.asarray(r.location) - np.asarray(self.B[r.bone].location)
            # boneWorld_R = Rr · off_R⁻¹ ;  boneWorld_p = pr - boneWorld_R · off_t
            RwB = Rr @ off_R.T
            pwB = pr - np.einsum("fij,j->fi", RwB, off_t)
            Rb_ours[ours] = RwB @ self.bind[ours][0]
            pb_ours[ours] = pwB * sc
            mode_of[ours] = r.mode
        writes_q = set(Rb_ours)
        writes_t = {n for n, m in mode_of.items() if m == 1}
        # 骨纵向局部 Z 自转：与原生 bone_twist 锁同一定义；detwist 时只去自转、保骨方向与位置
        twist_stats = writeback_twist(Rb_ours, self.parents, self.bind, W,
                                      detwist=bool(getattr(a, 'writeback_detwist', False)))

        frames_q, moves = {}, {}
        n_degen = 0
        for n in writes_q:
            pn = self.parents.get(n)
            if pn is None:
                Rp = np.repeat(np.eye(3)[None], F, axis=0); pp = np.zeros((F, 3))
            else:
                Rp_fk, pp_fk = W[pn]
                Rp = Rb_ours[pn] if pn in writes_q else Rp_fk
                pp = pb_ours[pn] if pn in writes_t else pp_fk
            Rl = np.einsum("fji,fjk->fik", Rp, Rb_ours[n])       # Rpᵀ · R
            frames_q[n] = [tuple(x) for x in ms_to_qs(Rl)]
            if n in writes_t:
                tl = np.einsum("fji,fj->fi", Rp, pb_ours[n] - pp)
                rt = np.asarray(rest[n].get("t", (0, 0, 0)), dtype=np.float64)
                L = np.linalg.norm(rt)
                if L < 1e-6:
                    n_degen += 1
                    continue
                u = rt / L
                d = tl - rt
                d = d - np.outer(d @ u, u)                          # 保长：去掉沿骨轴分量
                moves[n] = [tuple(x) for x in (rt + d)]

        # ---- 可选：物理骨局部量零相位低通（用户口径：0.5Hz 以上的次级运动读作抖动）。
        # 只动物理写回的通道，身体 FK 不变；非循环动画镜像延拓保端点。
        lowpass_stats = None
        cutoff = getattr(a, 'writeback_lowpass_hz', None)
        if cutoff:
            fps_anim = float(meta["fps"][0]) if isinstance(meta.get("fps"), (list, tuple)) else float(meta["fps"])
            lowpass_stats = lowpass_local_rotations(frames_q, moves, fps_anim, float(cutoff), bool(loop))

        # ---- 循环接缝：已在刚体解上交叉淡入（见上）。这里只量骨局部量的首尾差与**接缝速度跳变**
        # （尾步长与接缝步长之差），后者才是「接缝不自然」的判据：旧法首尾差 0.04° 但跳变 1.6°/帧。
        seam_txt = ""
        if K:
            b4, af = seam_b4, seam_af
            kick = 0.0
            for n, ql in frames_q.items():
                v_seam = q_angle_deg(ql[-1], ql[0]); v_tail = q_angle_deg(ql[-1], ql[-2]); v_head = q_angle_deg(ql[1], ql[0])
                kick = max(kick, abs(v_seam - v_tail), abs(v_head - v_seam))
            seam_txt = f"  接缝 {b4:.2f}°→{af:.2f}° 跳变 {kick:.2f}°/帧"

        # 真实分离率（**投影前**）：骨的刚体解位置到父骨当帧位置的距离 / rest 骨长。
        # metrics 里按 moves 算的那个是保长后的横向位移，永远 ≥0 且不代表拉伸。
        real_sep = {}
        for n in writes_t:
            pn = self.parents.get(n)
            if pn is None:
                continue
            pp = pb_ours[pn] if pn in writes_t else W[pn][1]
            L0 = np.linalg.norm(rest[n].get("t", (0, 0, 0)))
            if L0 < 1e-6:
                continue
            real_sep[n] = (np.linalg.norm(pb_ours[n] - pp, axis=1) / L0 - 1.0) * 100
        stats = self.metrics(frames_q, moves, rest, F, real_sep)
        if a.debug_top:
            worst = sorted(((float(np.abs(v).max()), n) for n, v in real_sep.items()), reverse=True)[:a.debug_top]
            log("       分离率最差：")
            for mx, n in worst:
                pn = self.parents.get(n)
                pi = self.ours_to_pmx_idx.get(n)
                pj = self.B[pi].parent if pi is not None else None
                pmx_par = self.pmx_to_ours.get(self.B[pj].name) if (pj is not None and pj >= 0) else None
                same = "同" if pmx_par == pn else f"PMX父={pmx_par}"
                log(f"         {n:40s} |峰| {mx:7.1f}%  mesh父={pn} ({same})  父由{'刚体' if pn in writes_t else 'FK'}定位")
            # 关节限位 vs 观测：按组给 PMX 角限位中位与观测摆角中位
            lim = {}
            for j in self.model.joints:
                if j.dest_rigid is None:
                    continue
                n = self.rigid_bone[j.dest_rigid]
                if n is None:
                    continue
                lim[n] = max(abs(x) for x in list(j.minimum_rotation) + list(j.maximum_rotation))
            for g in stats:
                ls = [math.degrees(lim[n]) for n in frames_q if n in lim and (next((k for k in GROUP_KEYS if k in n.lower()), "other") == g)]
                if ls:
                    log(f"       {g:6s} PMX 角限位 中位 {np.median(ls):5.1f}° 最大 {max(ls):5.1f}°  vs 观测摆角中位 {stats[g]['swing_med']:5.1f}°")

        # ---- 按蒙皮归属分流写回
        # 归属：本骨有蒙皮就按蒙皮；零蒙皮的链首/中间骨沿子链找第一根有皮的，再沿父链找。
        # 曾有 anim_writeback.build_chains（按名字分组，引用已退役的物理组合同），2026-09-06 已切掉。
        kids = {}
        for n2, pn2 in self.parents.items():
            kids.setdefault(pn2, []).append(n2)

        def own(n2):
            o = self.owner.get(n2)
            if o:
                return o
            stack = list(kids.get(n2, []))
            while stack:
                c = stack.pop()
                if self.owner.get(c):
                    return self.owner[c]
                stack.extend(kids.get(c, []))
            pn2 = self.parents.get(n2)
            while pn2:
                if self.owner.get(pn2):
                    return self.owner[pn2]
                pn2 = self.parents.get(pn2)
            return None
        # ---- 容差摘通道（阶段三第 2 项，用户 2026-09-06 同意）
        # 全程偏离时间均值不超过阈值的通道压成**常量**（均值），write_anim 里的 _drop_constant
        # 随后把恒定通道无损地收进 rest。**不能直接删通道**：物理骨基础版 rest 是竖直的，稳态
        # 已下垂 20° 但不动的头发删了通道就弹回竖直；写成常量保住下垂姿态。
        # 实测 12 条 body：t 通道 96% / q 通道 21% 可摘，body 体积约减半。
        n_dq = n_dt = 0
        if a.tol_q > 0:
            for n, ql in list(frames_q.items()):
                Q = np.asarray(ql, dtype=np.float64)
                for i in range(1, len(Q)):
                    if np.dot(Q[i], Q[0]) < 0:
                        Q[i] = -Q[i]
                m = Q.mean(axis=0); m /= max(np.linalg.norm(m), 1e-12)
                if max(q_angle_deg(q, m) for q in Q) < a.tol_q:
                    frames_q[n] = [tuple(m)] * len(ql); n_dq += 1
        if a.tol_t > 0:
            for n, tl in list(moves.items()):
                T = np.asarray(tl, dtype=np.float64); m = T.mean(axis=0)
                if np.abs(T - m).max() < a.tol_t:
                    moves[n] = [tuple(m)] * len(tl); n_dt += 1

        mine = {n: q for n, q in frames_q.items() if own(n) in (None, cfg["torso"])}
        theirs = {n: q for n, q in frames_q.items() if n not in mine}
        # 双侧写回（2026-10-12 锁暝/今汐「发尾的球没跟着动」）：发尾的铃铛/辫子饰品顶点在 torso mesh，
        # 蒙它的马尾/ponytail 骨在 head mesh 也带皮，skin_owner_map 先到先得判给 head，物理通道只进
        # head anim，torso 上的球停在 rest（dance_spin 两侧同名骨差 100+）。开关打开时，凡 torso 也带皮的
        # head 侧物理骨连同它的物理祖先骨，同时写进 body anim。默认关 = 旧行为逐字节不变。
        dual_n = 0
        if getattr(a, 'dual_side_writeback', False):
            torso_skinned = BPI.skin_owner_map((cfg["torso"],))
            body_names = {b["name"] for b in bones}
            extra = set()
            for n in theirs:
                if n in torso_skinned and n in body_names:
                    p = n
                    while p in theirs and p in body_names and p not in extra:
                        extra.add(p)
                        p = self.parents.get(p)
            for n in extra:
                mine[n] = theirs[n]
            dual_n = len(extra)
        mine_t = {k: v for k, v in moves.items() if k in mine}
        theirs_t = {k: v for k, v in moves.items() if k in theirs}
        out_dir.mkdir(parents=True, exist_ok=True)
        wrote = []
        if mine:
            BPI.write_back(out_dir / f"{base}.anim", meta, bones, ts, qs, F, mine, absolute=True, sway_moves=mine_t)
            wrote.append(f"{base}.anim({len(mine)}q/{len(mine_t)}t{f' 双侧{dual_n}' if dual_n else ''})")
        # theirs 为空（head 侧全部退出解算，例如工具里勾掉所有头发）时也要写 head:
        # 预览端固定要读 head+body 两个文件，缺一个就整份加载失败，画面上表现成
        # 「连裙子也没有物理」，与「物理没跑」无法区分。照抄基础 head = 零物理通道。
        cp_head = BPI.companion_path(base, cfg["head"], source_dir=prepared.get("source_dir"))
        head_output_name = cp_head.name if cp_head is not None else None
        if head_output_name and head_output_name.startswith("moe_head_"):
            head_output_name = cfg["head_prefix"] + head_output_name[len("moe_head_"):]
        if not theirs and cp_head is not None:
            cm, cb, cs = RV.load_anim(cp_head)
            cF, cts, cqs = RV.unpack_samples(cm, cb, cs)
            head_observable = prepared.get("head_observable_bones")
            if head_observable is not None and [b["name"] for b in cb] != prepared.get("head_observable_bone_table"):
                raise ValueError("Head animation bone table differs from its observable mesh contract")
            BPI.write_back(out_dir / head_output_name, cm, cb, cts, cqs, cF, {}, absolute=True,
                           observable_bones=head_observable)
            wrote.append(f"{head_output_name}(0q/0t 无物理骨)")
        if theirs:
            cp = cp_head
            if cp is None:
                log(f"  !! {base}: 无配对 head，{len(theirs)} 骨丢弃")
            else:
                cm, cb, cs = RV.load_anim(cp)
                cF, cts, cqs = RV.unpack_samples(cm, cb, cs)
                cn = {x["name"] for x in cb}
                # **保留对方帧数**（[[ck3-companion-write-must-keep-own-framecount]]），按归一化时间重采样
                sub = {k: BPI.resample(v, cF) for k, v in theirs.items() if k in cn}
                sub_t = {k: BPI.resample(v, cF) for k, v in theirs_t.items() if k in cn}
                head_observable = prepared.get("head_observable_bones")
                if head_observable is not None and [b["name"] for b in cb] != prepared.get("head_observable_bone_table"):
                    raise ValueError("Head animation bone table differs from its observable mesh contract")
                BPI.write_back(out_dir / head_output_name, cm, cb, cts, cqs, cF, sub, absolute=True,
                               sway_moves=sub_t, observable_bones=head_observable)
                wrote.append(f"{head_output_name}({len(sub)}q/{len(sub_t)}t)")
        log(f"[bake] {base}: {F}帧 {'循环×%d' % a.warmup if loop else '静置%d' % a.settle}"
            f"  q {len(frames_q)} t {len(moves)}{'（零长跳过 %d）' % n_degen if n_degen else ''}"
            f"{'  容差摘 q%d/t%d' % (n_dq, n_dt) if (n_dq or n_dt) else ''}"
            f"{'  角阻尼夹 %d' % sc_info['ang_capped'] if sc_info['ang_capped'] else ''}"
            f"{'  手臂屏蔽 %d' % sc_info['arm_shielded'] if sc_info['arm_shielded'] else ''}"
            f"{'  驱动×%.2f' % g_used if g_used != 1.0 else ''}{seam_txt}  解算 {t_sim:.1f}s 总 {time.time() - t0:.1f}s  → {' + '.join(wrote)}")
        for g, row in stats.items():
            log(f"       {g:6s} 骨{row['n']:4d}  摆角 中位 {row['swing_med']:6.2f}° p95 {row['swing_p95']:6.2f}°"
                f"  动态摆幅 中位 {row['dyn_med']:5.2f}° 最大 {row['dyn_max']:5.2f}°"
                f"  抖动 中位 {row['jit_med']:5.2f} 最大 {row['jit_max']:6.2f}"
                f"  分离率 |中位| {row['sep_med']:6.2f}% 拉长峰 {row['sep_max']:7.2f}% 压缩峰 {row['sep_min']:7.2f}%")
        if proc.stdout.strip():
            log("       " + proc.stdout.strip())
        if seam_txt:
            stats["_seam"] = {"before": round(b4, 3), "after": round(af, 3), "kick": round(kick, 3)}   # before 大 = 预热圈没收敛
        stats["_drive"] = round(g_used, 3)
        if dual_n:
            stats["_dual_side"] = dual_n
        stats["_twist"] = twist_stats
        if lowpass_stats is not None:
            stats["_lowpass"] = lowpass_stats
            log(f"       低通 {lowpass_stats['cutoff_hz']}Hz 去掉 p50/p95/最大 "
                + '/'.join(f'{x:.2f}' for x in lowpass_stats['removed_p50_p95_max_deg']) + "°")
        log(f"       自转 写回前 最大 {twist_stats['before_max_deg']:.3f}° p95 {twist_stats['before_p95_deg']:.3f}°"
            f"  写回后 最大 {twist_stats['after_max_deg']:.3f}°  detwist={'开' if twist_stats['detwist'] else '关'}")
        if solver_report is not None:
            stats['_solver'] = solver_report
        return stats

    @staticmethod
    def metrics(frames_q, moves, rest, F, real_sep):
        """A/B 判据：按组统计摆角（相对该骨 .anim rest）、**动态摆幅**与**真实**分离率（投影前，
        可正可负，正 = 被拉长）。

        「摆角」含重力下垂（rest 是竖直、稳态已经垂下 20°），量不出「有没有在动」；
        风场的目标「头发摆到 10°」要看**围绕时间均值的偏离**：每骨先取逐帧四元数的时间均值
        （符号对齐后平均再归一），再算每帧到均值的夹角，取该骨 p95 为它的动态摆幅；
        组内报中位与最大。躯干不动时这就是风给的摆幅；躯干动时含跟随。
        """
        rows = {}
        for n, ql in frames_q.items():
            key = next((k for k in GROUP_KEYS if k in n.lower()), "other")
            row = rows.setdefault(key, {"n": 0, "sw": [], "sep": [], "dyn": [], "jit": []})
            row["n"] += 1
            rq = np.asarray(rest[n]["q"], dtype=np.float64)
            Q = np.asarray(ql, dtype=np.float64)
            row["sw"].extend(q_angle_deg(q, rq) for q in Q)
            for i in range(1, len(Q)):
                if np.dot(Q[i], Q[0]) < 0:
                    Q[i] = -Q[i]
            m = Q.mean(axis=0); m /= max(np.linalg.norm(m), 1e-12)
            row["dyn"].append(float(np.percentile([q_angle_deg(q, m) for q in Q], 95)))
            if len(Q) >= 4:
                v = np.asarray([q_angle_deg(Q[i], Q[i - 1]) for i in range(1, len(Q))])
                j = float(np.percentile(np.abs(np.diff(v)), 95))
                row["jit"].append(j)
                if j > row.get("jit_worst", (None, -1.0))[1]:
                    row["jit_worst"] = (n, j)
            if n in real_sep:
                row["sep"].extend(real_sep[n].tolist())
        out = {}
        for k, row in sorted(rows.items()):
            sw = np.asarray(row["sw"]); sep = np.asarray(row["sep"]) if row["sep"] else np.zeros(1)
            dyn = np.asarray(row["dyn"]); jit = np.asarray(row["jit"]) if row["jit"] else np.zeros(1)
            out[k] = {"n": row["n"], "swing_med": float(np.median(sw)), "swing_p95": float(np.percentile(sw, 95)),
                      "jit_med": float(np.median(jit)), "jit_max": float(jit.max()),
                      "jit_worst": row.get("jit_worst", (None, 0.0))[0],
                      "swing_max": float(sw.max()), "dyn_med": float(np.median(dyn)), "dyn_max": float(dyn.max()),
                      "sep_med": float(np.median(np.abs(sep))),
                      "sep_max": float(sep.max()), "sep_min": float(sep.min())}
        return out


def euler_yxz_q(e):
    """PMX 欧拉 (x,y,z) → (x,y,z,w)，YXZ 顺序，与 main.cpp 的 eulerYXZ 完全一致。"""
    def ax(v, ang):
        s = math.sin(ang / 2)
        return (v[0] * s, v[1] * s, v[2] * s, math.cos(ang / 2))
    qx, qy, qz = ax((1, 0, 0), e[0]), ax((0, 1, 0), e[1]), ax((0, 0, 1), e[2])

    def mul(a, b):
        ax_, ay, az, aw = a; bx, by, bz, bw = b
        return (aw * bx + ax_ * bw + ay * bz - az * by, aw * by - ax_ * bz + ay * bw + az * bx,
                aw * bz + ax_ * by - ay * bx + az * bw, aw * bw - ax_ * bx - ay * by - az * bz)
    return mul(mul(qy, qx), qz)


# ------------------------------------------------------------------ main
# ---- 进程池 worker（2026-09-07）。ThreadPool 共享 GIL：每条动画一半时间在 Python 后处理，12 job 只有 6~7 个
# 解算器同时在跑、16 核 57~63%。每个进程自己装一份 Rig，bake 结果（纯 dict）回传，异常回传成字符串不终结整批。
_W = {}


def _worker_init(cfg, out_dir, a):
    _W["rig"] = Rig(cfg); _W["out"] = out_dir; _W["a"] = a


def _worker_one(p):
    try:
        return p.stem, _W["rig"].bake_one(p, _W["out"], _W["a"]), None
    except Exception as e:
        import traceback
        return p.stem, None, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ck", required=True, choices=list(REPORTS))
    ap.add_argument("--anim", action="append", default=[], help="state 基名（不带 moe_xxx_body_ 前缀）或完整基名")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--profile", help="可视化调试器保存的物理参数 JSON")
    ap.add_argument('--release-recipe', action=argparse.BooleanOptionalAction, default=True,
                    help='Use the reviewed character source/action recipe; --no-release-recipe isolates experiments')
    ap.add_argument("--io-dir", help="独立试烘的中间文件目录（必须在 work 下）")
    ap.add_argument("--exe", default=None, help="覆盖所选后端的可执行文件路径")
    ap.add_argument('--backend', choices=('bullet', 'physx-cpu', 'physx-gpu'), default='bullet')
    ap.add_argument('--physx-allow-angular-approximation', action='store_true',
                    help='显式试用 D6 swing/twist 近似；与 PMX/Bullet 组合欧拉角限位不等价')
    ap.add_argument('--physx-contact-frequency-hz', type=float, default=0,
                    help='PhysX 接触弹簧频率，0保留刚性接触；非零采用质量无关的有限柔度')
    ap.add_argument('--physx-contact-damping-ratio', type=float, default=1,
                    help='有限柔度接触的阻尼比，1为临界阻尼')
    ap.add_argument('--physx-contact-rigids',
                    help='可选逗号分隔的源刚体索引，仅这些刚体材质使用柔性接触；必须同时指定非零接触频率')
    ap.add_argument('--physx-solver', choices=('tgs', 'pgs'), default='tgs',
                    help='PhysX 约束算法；默认保持既有 TGS')
    ap.add_argument('--physx-angular-limit-rigids',
                    help='仅柔化两端都在该刚体索引列表中的动态有限角关节，支持新发束窄角限位；需非零角限位频率')
    ap.add_argument('--physx-external-forces-every-iteration', action='store_true',
                    help='在每个 TGS 位置迭代子步分配重力和外力')
    ap.add_argument('--batch-size', type=int, default=1, help='PhysX 同一场景中的独立动作数；Bullet 保持 --jobs')
    ap.add_argument('--simulate-follow-bones', action='store_true',
                    help='隔离试验：恢复登记表 follow_bone 部件的源物理，只能输出到 work/')
    ap.add_argument('--physx-iters', type=int, default=8, help='TGS 位置迭代，与 Bullet --iters 分开')
    ap.add_argument('--physx-velocity-iters', type=int, default=1)
    ap.add_argument('--physx-threads', type=int, default=1)
    ap.add_argument('--physx-contact-offset', type=float, default=0.04, help='PMX 单位；不是 Bullet linear_slop')
    ap.add_argument('--physx-drive-damping', type=float, default=1.0, help='PMX 量纲下的弹簧阻尼系数')
    ap.add_argument('--physx-angular-limit-hz', type=float, default=0,
                    help='隔离试验：宽角限位的临界阻尼响应频率；0 保持硬限位，不等价于 Bullet ERP')
    ap.add_argument('--physx-disable-preprocessing', action='store_true',
                    help='关闭 PhysX 关节约束预处理，检验密集闭环约束的稳定性')
    ap.add_argument('--physx-max-depenetration-velocity', type=float, default=10.0,
                    help='PMX 单位/秒；限制初始穿透修正注入的速度，仍保留碰撞')
    ap.add_argument("--warmup", type=int, default=3,
                    help="循环动画预热圈数（默认 3）。每圈从上一圈末态继续（main.cpp:364），圈数决定裙子有没有真正落到腿上。2026-09-06 曾为省 ~40%% 解算降到 1，2026-09-15 用户指出「不加预热裙子甚至都不正确在腿上」改回 3")
    ap.add_argument("--intro", type=int, default=60,
                    help="入场过渡动画帧数（默认 60 = 2 s @30fps；0 关）。运动学墙从 PMX rest 插值到第 0 帧，动态刚体从 rest 开始响应；时间单位与 --hz 物理子步分开。")
    ap.add_argument("--intro-deflate", type=float, default=None,
                    help="入场期把运动学墙缩到这个比例，过渡结束线性恢复到 1.0（None/1.0 = 不缩）。抄 Houdini Vellum Drape 的 Inflate Collisions：初始布料已与碰撞体相交时先让碰撞体瘪下去、再膨胀回原尺寸，得到无穿透的垂落结果。坐姿正是「初始已相交」那一类")
    ap.add_argument("--contact-stiffness", type=float, default=None,
                    help="接触刚度（Bullet setContactStiffnessAndDamping，只给动态刚体）。静止接触抖动是 Bullet 有编号的已知问题（issue #3049）：顺序冲量解算器加穿透恢复项在接触面上「推出 vs 拉回」打架。这个量把接触当弹簧-阻尼器，比 --erp2 更贴近问题。None = 用默认刚性接触")
    ap.add_argument("--contact-damping", type=float, default=None,
                    help="接触阻尼，与 --contact-stiffness 配对使用")
    ap.add_argument("--intro-hold", type=int, default=0,
                    help="入场过渡结束后在第 0 帧姿态原地静置的动画帧数（默认 0 = 关）。与 --hz 物理子步分开。")
    ap.add_argument("--settle", type=int, default=180,
                    help="非循环动画第 0 帧静置动画帧数（默认 180 = 6 s @30fps）。循环动画使用 --warmup，非循环使用 --settle；与 --hz 物理子步分开。")
    ap.add_argument("--loop-tail", type=int, default=12,
                    help="循环接缝交叉淡入帧数：解算器多录下一圈开头 K 帧，与本圈开头混合（0 = 不处理；最多半圈，少于4帧关闭）。"
                         "2026-09-07 前是尾段单侧拉向首帧，位置对了速度断掉")
    ap.add_argument("--gravity", type=float, default=None, help="覆盖 saba 默认 -98（PMX 尺度）")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--tol-q", type=float, default=0.5, help="全程转角偏离 < 该度数的物理 q 通道压成常量（0 关）")
    ap.add_argument("--tol-t", type=float, default=0.05, help="全程位移偏离 < 该值（我们空间）的物理 t 通道压成常量（0 关）")
    # 2026-09-06 洛茜 dance_spin（尾巴最差动画，腿墙扫过尾根）上 A/B 定的两个默认：
    #   迭代 10/30/60 → 尾巴稳态分离中位 2.30/0.42/0.09%，idle 0.68→0.02%，头发摆幅不变，
    #     耗时 ×2~3（质量 > 时间）；子步 120/240/480 Hz 对 46% 峰无效（45.6/45.0），保持 120
    #   球径上限 无/1.0/0.8/0.5 → 峰 46/9.7/11.3/27%，尾巴摆角 0.59/1.46/2.09/5.06°。
    #     **默认关**：用户 2026-09-06「尾巴适当拉长先看看效果，不明显就这样，批量模型不追求每个完美」。
    #     要开就 --ball-cap 1.0（A/B 里最好的一档）
    ap.add_argument("--iters", type=int, default=60, help="Bullet 求解器迭代数（默认 60；saba 用 10）")
    ap.add_argument("--hz", type=float, default=120, help="固定子步频率（saba/默认 120；A/B 过 240/480 无效）")
    ap.add_argument("--ball-cap", type=float, default=None,
                    help="动态球体半径 ≤ 该倍数×到父骨间距（默认关 = saba 原尺寸；1.0 是 A/B 最好的一档）")
    # 风场（阶段二，2026-09-06 在洛茜 idle 上 A/B 定参）。**默认加速度 25**：
    #   力模式（F 直接是力）：0.06 质量的斗篷被吹到 54°、拉伸 22.9%，头发才 12° —— 淘汰
    #   加速度模式 A10/20/25/30 → 头发动态摆幅中位 4.4/9.3/9.9/10.5°，斗篷/尾巴/裙子几乎不动
    # 目标是用户定的「待机头发摆到 10° 左右」→ A25。`--wind 0` 回到纯 saba 基线。
    # 2026-09-06 用户看过实机后先降到 17.5 / 周期 5 s，再看一轮后决定**先去掉风场**（默认 0）。
    # A/B 结论（加速度 25 ≈ 待机头发 10°）保留在 todo §3，要开就 --wind 25 --wind-period 2.5。
    ap.add_argument("--wind", type=float, default=0.0, help="风强（默认 0 关闭；支持 Bullet/PhysX；加速度模式单位 PMX/s²）")
    ap.add_argument("--wind-period", type=float, default=5.0, help="主风周期秒数，循环按各动作长度折成整数圈；PhysX 短循环减弱阵风")
    ap.add_argument("--wind-turb", type=float, default=0.35, help="侧向湍流相对主风的比例")
    ap.add_argument("--wind-base", type=float, default=0.5, help="主风常量分量（0=纯正弦来回，1=恒定风）")
    ap.add_argument("--wind-dir", type=float, nargs=3, default=(1.0, 0.0, 0.4), help="主风向（PMX 空间，x 右 z 前）")
    ap.add_argument("--wind-accel", action=argparse.BooleanOptionalAction, default=True,
                    help="按质量给加速度（默认）；--no-wind-accel 给力（重挂件不受风，轻斗篷被吹翻，A/B 已淘汰）")
    ap.add_argument("--debug-top", type=int, default=0, help="打印真实分离率最差的 N 根骨与各组限位对照")
    # 2026-09-06 用户两问：手臂嵌头发/裙子的高频抖动与拉伸；飘动僵死。A/B 变量：
    ap.add_argument("--ang-damp-cap", type=float, default=None,
                    help="动态刚体角阻尼上限（PMX 原值 ≥1 被 Bullet 夹成 1 = 每子步角速度清零；0.9 时一秒衰到 10%%）")
    ap.add_argument("--wall-fit", default=None,
                    help="按 rest 穿透量收缩这些组的球/胶囊墙半径，如 \"7\"；不传则读登记表 phys_wall_fit。替代整体免碰，坐姿仍有腿撑裙")
    ap.add_argument("--wall-fit-margin", type=float, default=0.02)
    ap.add_argument("--wall-fit-min", type=float, default=0.5, help="墙半径最多缩到原值的该比例")
    ap.add_argument("--no-collide-groups", default=None,
                    help="PMX 碰撞组对，双向免碰，如 \"2:7,2:4\"；不传则读登记表 phys_nocollide（A/B 覆盖用）")
    ap.add_argument("--contact-report", action="store_true",
                    help="把解算器的接触报告（每对刚体的接触帧数与 Bullet manifold 上的 max_depth）"
                         "写到中间件目录 <base>.contacts.csv。量 split impulse 阈值判据用："
                         "穿透浅于 m_splitImpulsePenetrationThreshold（默认 0.04）的接触，"
                         "位置修正项会被当成真实速度冲量注入 = 推出时带多余动能")
    ap.add_argument("--arm-shield", type=float, default=0.5,
                    help="FK 下与手臂墙重叠帧占比 ≥ 该值的挂件刚体对手臂墙免碰（按动画、按刚体；0 关；建议 0.5）")
    ap.add_argument("--erp2", type=float, default=0.1, help="接触约束误差修正系数（默认 0.1，A/B 定；Bullet 原值 0.2；小 = 推出更软）")
    ap.add_argument("--erp", type=float, default=None, help="关节约束误差修正系数（Bullet 默认 0.2）")
    ap.add_argument("--split-impulse-threshold", type=float, default=None,
                    help="传正数 T，解算器写成 m_splitImpulsePenetrationThreshold = -T（Bullet 默认 0.04）。"
                         "穿透浅于它的接触，位置修正项 -penetration*erp/dt 被当成真实速度冲量注入 = "
                         "推出时带多余动能 = 弹起/落回/再嵌入的极限循环；深于它才走不污染速度的 push 通道。"
                         "0.04 是 Bullet 按米制标的，我们是 PMX 尺度（重力 98、裙格半径 0.45）等于紧一个量级。"
                         "实测（jia manifold 真实穿透量）「浅且久」接触对：坐姿 32、idle 27、dance_spin 0，"
                         "与抖动单调对应。往 0 挪 = 让浅接触也走 push 通道")
    ap.add_argument("--linear-slop", type=float, default=0.03,
                    help="m_linearSlop（Bullet 默认 0.0 = 要求把穿透精确压到零，求解器一直在争最后那一点）。"
                         "**我们默认 0.03**（2026-09-16 绛雨 A/B 定，用户「全部开吧」）：末端位移 p95 "
                         "坐姿 -29%%（kick 26.2→18.2 首次破 20）、idle -15%%、dance_spin -25%%；"
                         "唯一回归是 writing（裙子本就静垂）0.0131→0.0489，但 >2°/>5° 占比仍近零。"
                         "0.06 与 0.03 打平，取 0.03 = 一半穿透代价。传 0 回到 Bullet 原值")
    ap.add_argument("--erp-limit-skip", default=None,
                    help="逗号分隔的骨名子串，含它的关节不参与 --erp-limit 软化（默认 None = 读登记表 moe_chars.phys_erp_skip；"
                         "洁尔佩塔登记了 _qb_,_qj_ 飘带；传空串 = 都软化）。曾怀疑洛茜 prostration_loop 裙子 "
                         "11.6%%→227%% 是裙子被软化所致，排除裙子仍 311%%；与 ERP 无关的微扰（erp 0.19 / 增益 8.001 / 120.01 Hz / 迭代 61）"
                         "全部 228~305%%——基线 11.6%% 是刀锋态上的运气解，不是回归。开关保留供 A/B")
    ap.add_argument("--erp-limit-min-range", type=float, default=6.0,
                    help="角轴全范围（度）小于此值按锁死对待、不软化（默认 6：洛茜裙子 ±2.5° 排除，头发/刘海 ±10° 纳入）。"
                         "裙子关节不是 6 轴锁死而是 ±2.5°，软化后跪拜时被腿墙推着漂移，prostration_loop 分离率 11.6%%→227%%")
    ap.add_argument("--erp-limit", type=float, default=0.02,
                    help="只给有活动范围的关节轴设限位 ERP（默认 0.05，2026-09-07 三张矩阵定；锁死轴保持 --erp/0.2；负数 = 不设）。"
                         "抖动根因：骨顶在角限位上时限位行按世界 ERP 0.2 一子步硬弹回 = 单帧 10~57° 踢击后衰减（3~6 Hz 随躯干节律，不是 15 Hz 颤）。"
                         "全局降 --erp 会连锁死关节一起软化：洛茜裙子全是 6 轴锁死关节，ERP≤0.1 时接触压过关节、跪拜分离率 74%%→153%%；"
                         "0.05 让耳/发/帽/爪抖动降到 1/2~1/3、摆幅留七成、帽/发分离率不变；0.02 摆幅只剩一半太死。"
                         "已知代价：±10° 贴身体墙的小关节（芙洛伦刘海、洛茜袖口）在骑马/跪拜上陷墙换成接触踢击，单骨抖动 11→20"
                         " 2026-09-08 用户「洛茜耳尖/裙边高频抖动」：三条 idle 上 A/B，0.05→0.02 踢击帧(|Δ²θ|>0.5°) 耳 21%→6.7%、帽 3.8→1.1、发 2.8→1.1、爪 8.5→6.0，"
                         "摆幅 p95 只掉 4~12%、分离峰不变；0 锁死耳朵且裙子分离峰 1.7→74。默认改 0.02。接触 ERP 对此无关；驱动增益 1/4/8 耳踢击 3.7/9.0/21%（增益把耳甩到 ±50° 限位上）")
    ap.add_argument("--spring2", action=argparse.BooleanOptionalAction, default=True,
                    help="关节用隐式弹簧 Spring2（默认开，A/B 定；--no-spring2 回显式弹簧。錘弹簧 1e5 在 1/120 s 下 k·dt²/m=6.9>4 会自激）")
    ap.add_argument("--stats-json", default=None, help="把每条动画的分组指标（含本次参数）写成 JSON，A/B 矩阵用")
    # 驱动放大（2026-09-06，风场替代）：默认 1 = 关；A/B 在芙洛伦 idle 上定。
    ap.add_argument("--drive-gain", type=float, default=DEFAULT_SOLVER['drive_gain'],
                    help="待机飘动：躯干运动放大倍数（只影响解算与物理骨参考，不改身体通道；1 = 关）")
    ap.add_argument("--drive-thresh", type=float, default=1.5,
                    help="body_root 位移峰 ≤ 此值取满 gain，≥ 2 倍此值不放大（芙洛伦 345 条里 277 条 <1.5）")
    a = ap.parse_args()
    if a.profile:
        from physics_profile import load
        profile = load(a.profile, a.ck)
        for key, value in profile.get('solver', {}).items():
            setattr(a, key, int(value) if key in ('iters', 'warmup') else value)
    else:
        profile = None
    if a.io_dir and not Path(a.io_dir).resolve().is_relative_to((ROOT / 'work').resolve()):
        raise SystemExit('--io-dir 必须位于项目 work 目录内')
    if a.simulate_follow_bones and not Path(a.out_dir).resolve().is_relative_to((ROOT / 'work').resolve()):
        raise SystemExit('--simulate-follow-bones 试验输出必须位于 work/')
    if a.batch_size < 1 or a.batch_size > 1024 or (a.backend == 'bullet' and a.batch_size != 1):
        raise SystemExit('--batch-size 仅 PhysX 支持 1..1024')
    if a.backend != 'bullet':
        if not a.physx_allow_angular_approximation:
            raise SystemExit('PhysX 原型需要 --physx-allow-angular-approximation；组合旋转语义仍需验收')
        if not Path(a.out_dir).resolve().is_relative_to((ROOT / 'work').resolve()):
            raise SystemExit('PhysX 原型输出必须在 work/ 下，尚不能作为正式物理包')
        if a.jobs != 1:
            raise SystemExit('PhysX 原型使用 --batch-size；--jobs 保持 1，避免多个 CUDA 进程争用')
        unsupported = [key for key in ('erp', 'split_impulse_threshold', 'contact_stiffness', 'contact_damping')
                       if getattr(a, key) is not None]
        bullet_defaults = dict(iters=60, erp2=0.1, linear_slop=0.03, erp_limit=0.02,
                               erp_limit_min_range=6.0, spring2=True)
        unsupported += [key for key, default in bullet_defaults.items() if getattr(a, key) != default]
        if a.intro_deflate is not None and a.intro_deflate != 1:
            unsupported += ['intro_deflate']
        if unsupported:
            raise SystemExit(f'PhysX 尚未映射这些选项：{unsupported}')
        log('[PhysX prototype] D6 swing/twist 近似；各轴角限位翻号。Bullet ERP/slop/spring2 不映射；'
            '使用独立 PhysX 迭代/接触参数，SDK 错误与警告会拒绝产物。')
    a.exe = Path(a.exe or (EXE if a.backend == 'bullet' else PHYSX_EXE))
    if not a.exe.is_file():
        build = 'tools/physics/moe_bullet/build.ps1' if a.backend == 'bullet' else 'python tools/physics/moe_physx/build.py'
        raise SystemExit(f"**找不到解算器** {a.exe} —— 先跑 {build}")
    cfg = char_config(a.ck)
    cfg['profile'] = profile
    cfg['release_recipe'] = a.release_recipe
    cfg['simulate_follow_bones'] = a.simulate_follow_bones
    cfg["ball_cap"] = a.ball_cap or None
    rig = Rig(cfg) if a.jobs <= 1 else None   # 多进程时每个 worker 自己装
    adir = MP.require_base_anims(a.ck, verify_hashes=True)
    targets = []
    for t in a.anim:
        t = t.strip()
        p = adir / (f"{t}.anim" if t.startswith(cfg["body_prefix"]) else f"{cfg['body_prefix']}{t}.anim")
        if not p.is_file():
            raise SystemExit(f"**找不到动画** {p}")
        targets.append(p)
    if a.all:
        # **只跑登记进 .asset 的动画**：目录里有 17 个 `_var*` 孤儿文件（洛茜 2026-09-05），
        # 引擎从不播、多数没有 head 配对，烘它们只会在配对处抛错。判据用 is_looping 的表。
        is_looping("_")
        allf = sorted(adir.glob(f"{cfg['body_prefix']}*.anim"))
        targets = [p for p in allf if _strip_char_prefix(p.stem) in _LOOP]
        if len(targets) != len(allf):
            log(f"[plan] 跳过 {len(allf) - len(targets)} 条未登记进 .asset 的动画: "
                f"{[p.stem.replace(cfg['body_prefix'], '') for p in allf if p not in targets][:6]} ...")
    if not targets:
        raise SystemExit("没有目标：给 --anim 或 --all")
    if len(set(targets)) != len(targets):
        raise SystemExit('目标动作重复；每个动作必须有独立的中间文件和输出')
    out_dir = Path(a.out_dir)
    log(f"[plan] {len(targets)} 条 → {out_dir}  jobs {a.jobs}")
    t0 = time.time()
    fails = []
    results = {}

    def _one(p):
        try:
            st = rig.bake_one(p, out_dir, a)
            results[p.stem] = st
            return st
        except Exception as e:   # 一条炸了不终结整批（ex.map 会），记下来最后汇报
            import traceback
            fails.append((p.stem, f"{type(e).__name__}: {e}"))
            log(f"  !! {p.stem}: {type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}")
            return None
    if a.backend != 'bullet' and a.batch_size > 1:
        for start in range(0, len(targets), a.batch_size):
            batch = targets[start:start + a.batch_size]
            try:
                batch_results, batch_failures = rig.bake_batch(batch, out_dir, a)
                results.update(batch_results)
                fails.extend(batch_failures)
            except Exception as e:
                fails.extend((p.stem, f'{type(e).__name__}: {e}') for p in batch)
                log(f'  !! batch: {e}')
    elif a.jobs > 1:
        with ProcessPoolExecutor(a.jobs, initializer=_worker_init, initargs=(cfg, out_dir, a)) as ex:
            for stem, st, err in ex.map(_worker_one, targets):
                if err:
                    fails.append((stem, err)); log(f"  !! {stem}: {err}")
                else:
                    results[stem] = st
    else:
        for p in targets:
            _one(p)
    log(f"[done] {len(targets)} 条 {time.time() - t0:.1f}s  失败 {len(fails)}"
        + ("".join(f"\n  !! {n}: {m}" for n, m in fails) if fails else ""))
    if a.stats_json:
        sj = Path(a.stats_json); sj.parent.mkdir(parents=True, exist_ok=True)
        sj.write_text(json.dumps({"args": {k: v for k, v in vars(a).items() if k != "exe"},
                                  "stats": results}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
