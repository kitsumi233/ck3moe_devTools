"""生成芙洛伦 PoC 的 CK3 mod 文件：portrait_type、asset、文化、genes、本地化。

核心验证点：CK3 是否接受 common/portrait_types/ 下新增的自定义类型。
原版只有一个 `human`，数据里没有选择条件，可能由引擎硬编码。

策略：每套模型使用独立 portrait group，自定义 head/torso 与骨架。
具有最高级美貌的角色可使用同一性别的模型，年龄不限（启用文化包时文化还需拥有 cosplay 认同传统）。

用法: python tools/build/build_moe_mod.py
"""
import json
import os
import re
import shutil
import pathlib
import subprocess
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim", "model")]   # 子目录化后底座不在脚本同目录；model/ 为 rename_mesh_object
import moe_paths as MP        # 资源目录的单点定义
import moe_out as O           # 产物归属的单点定义（谁写谁声明）
import moe_chars as X         # 角色登记表：加角色只改它
import moe_material_contract as MC   # no_render 等材质级渲染决策的唯一事实源
import moe_cosplay_rules as CR       # 文化传统准入及继承外观纠正
import moe_wardrobe as W             # 家族衣橱与服装解锁

# 就绪 = 有烘焙 mesh 的角色才进 TYPES / .asset；槽位号用登记表里的稳定序号。
ACTIVE = X.active()

ROOT = Path(__file__).resolve().parents[2]
POC = ROOT / "work" / "florence_poc"
# 单体 mod 名。已退役（2026-08-29，目录在 trash/20260829_monolithic_mod_
# retired/），只剩清单 json 里留个名字备查 —— **不要**再拿它拼路径。
MOD_NAME = "moe_florence_poc"
# 常规文档 mod 目录。**不再作为安装目标**（2026-08-23 用户决定：那份是仓库
# 的逐字节副本，维护两份只多一个「忘记同步」的失败点）。保留常量供参考。
CK3_MOD_DIR = Path(r"C:\Users\Administrator\Documents\Paradox Interactive\Crusader Kings III\mod")
# 实际安装目标：隔离 userdir 的 mod 目录（由 finalize_mods.py 统一部署）
INSTALL_MOD_DIR = Path(r"D:\有趣计划\CK3_MoeMod\work\ck3_florence_userdir\mod")
# 原版 game 目录。从这里实时读原版文件（animations.txt 覆盖版、
# 已有的人名本地化 key），CK3 更新后重跑脚本即可，不把改好的文件当源码维护。
GAME = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III\game")

# 文化 id。用户 2026-08-27 定名为 `moe`（此前叫 moe_florence —— 那时只有
# 一个角色，文化名跟着她起了，现在文化里有三个角色，名字不该绑在其中一个上）。
# 改名会连带改文件名、loc key、书签 id、贴图名，全部从这个常量派生，
# 没有第二处硬编码。
CULTURE = "moe"
FAITH_SKIP = True          # PoC 不做宗教，先只验证肖像
ENTITY_HEAD = "moe_head_entity"
ENTITY_TORSO = "moe_torso_entity"
# 衣服是**独立 entity**，不是 portrait type 的槽位。
# portrait type 只有 head / torso 两个实体字段；衣服靠 accessory 挂上去，
# accessory 里写 shared_pose_entity = torso 来共用躯干的骨架与姿势。
# 因此这个 entity **不能**有 state / skeletal_animation_set —— 原版
# f_clothes 的 .asset 就是这样（只有 pdxmesh + blend_shape + game_data）。
# 写了 state 反而会让衣服自己播一套动画，与躯干不同步。
ENTITY_CLOTHES = "moe_clothes_entity"
MESH_HEAD = "moe_head_mesh"
MESH_TORSO = "moe_torso_mesh"
MESH_CLOTHES = "moe_clothes_mesh"
ANIM_SET = "moe_common"

# ---------------------------------------------------------------- 立绘放大
# 用户 2026-08-18 要求「默认放很大」。
#
# ## pdxmesh scale 这条路走不通（2026-08-18 实测，已撤回）
#
# 试过 PORTRAIT_SCALE = 1.35 写进两个 pdxmesh 块。结果**头整体浮到身体上方**，
# 脖子处明显断开。量化对得上:
#
#   head.mesh 顶点 Y 83.46~143.96，×1.35 → 112.67~194.35
#   头上移 = (1.35-1) × 83.46 = 29.2 单位 ≈ 头高(81.7)的 36%
#
# 根因是我们的坐标约定与原版不同（这一点 10_moe_types.txt 的 attach 注释
# 已经记过，但当时没想到会牵连 scale）:
#
#   原版 head.mesh 顶点在**头骨局部空间**（Y 25~55），靠 attach 到胸椎
#   才升到世界位置。scale 缩放的是局部坐标，attach 偏移另算，所以头身同步。
#
#   我们的 head.mesh 是从同一具全身骨架拆出来的，顶点**已经是世界坐标**
#   （Y 83~144），attach 用同名骨互贴（bn_m_root → bn_m_root）做零位移。
#   scale 把那个 83.46 的基准偏移一起乘了 1.35 → 头被顶上去 29.2。
#
# 要在我们的约定下用 scale，得先把 head 顶点平移到局部空间、再用 attach
# 补回偏移 —— 那是改 build_florence_poc.py 的导出坐标系，会牵动
# 已经验证过的 attach / 骨数 / 动画一整条链。不值得为放大做这个。
#
# 所以放大改走**相机**（在我们自己的 mod 里加相机定义 + 覆盖 gui 里的
# 相机名引用），或者干脆让用户开一个放大立绘 mod。见 README
# 「立绘放大」一节的结论。
#
# 保留这个常量 = 1.0（与原版 female_head.asset 一致），别删 ——
# 留着是为了让下一个人看到上面这段推理，不要再试一次。
PORTRAIT_SCALE = 1.0

# 所有 portrait_* shader 都在这个文件里。shader 名取自
# game/tools/pdxexporter.settings 的 materials 白名单。
# 不指定 shader_file 时引擎默认找 gfx/FX/pdxmesh.shader，会加载失败。
#
# **指向 mod 自己的副本，不是原版** —— 原版只有 hair 系 Effect 声明了
# rasterizer_no_culling，skin/eye/attachment 全走默认背面剔除（单面），
# MMD 的开放曲面（裙摆/袖口/领口/飘带）会被看穿内侧。副本由
# tools/build/build_moe_shader.py 生成，给每个用到的 Effect 加上双面。
# CK3 更新 portrait.shader 后要重跑那个脚本（副本是冻结的）。
PORTRAIT_SHADER_FILE = "gfx/FX/jomini/portrait.shader"


def moe_shader_name(shader):
    """Use a Moe-only effect alias available in portrait and court shaders."""
    return shader if shader.startswith("moe_") else f"moe_{shader}"

# 「刻意不可见」层用的全透明贴图三件套，由 tools/textures/make_invisible_texture.py 生成。
# 三张都要给：缺 specular/normal 不是「用中性值」而是采样成白图。
INVISIBLE_TEX = "moe_invisible.dds"
INVISIBLE_TEX_PROPS = "moe_invisible_properties.dds"
INVISIBLE_TEX_NORMAL = "moe_invisible_normal.dds"

report = json.loads((POC / "florence_poc_report.json").read_text(encoding="utf-8"))
bones = report["final_bones"]
parts = report["parts"]

print(f"骨架 {len(bones)} 骨，部件 {list(parts)}")
print(f"语义合同满足: {report['semantic_contract']['satisfies_contract']}")


def w(rel, text, mod=None):
    """写一个产物，**当场声明它属于哪个 mod**。

    `mod` 省略时归核心（机制文件绝大多数属于核心）。逐角色的资产要显式
    传 `O.models_of(ck)`。

    为什么归属要写在这里而不是事后推断：原先是「合成进单体目录 → 481 行
    正则猜归属」，那个结构让换装 `.gui` 被路由进放大立绘 mod（只装核心 +
    模型包的用户点交互后什么都不弹），也让书签底图因为文件名恰好以 `flo`
    结尾而被当成角色资产。归属由写的人声明，这两类错就不可能发生。
    """
    mod = mod or O.CORE
    O.write(mod, rel, text)
    print(f"  写入 [{mod}] {rel}")


# ---------------------------------------------------------------- 目录清理
#
# **只清核心，并保护由后续生成器拥有的核心文件。** 这是直写子 mod 之后
# 最重要的一处收窄：以前这里
# `rmtree(整个单体 mod)`，于是任何一个后续步骤漏跑就静默丢功能 ——
# 2026-08-28 一天里丢过双面 shader、立绘放大、描边、肖像布光四样，
# 全都不报错，只是效果消失（见 moe_out.py 的模块 docstring）。
#
# 现在本脚本只负责核心 + 各模型 mod 的 `.asset`：
#   · 核心主体重写；道具基因、布光、换装宿主 GUI 与 descriptor 分别由后续
#     生成器拥有，重置前暂存、重置后原样恢复。否则单跑本脚本会静默拆掉
#     这些功能，直到完整 build_all 再跑一遍才回来。
#   · 模型 mod **不清**（里面是 mesh / 贴图 / 1417 个 .anim，几小时才能重做），
#     只覆盖写自己那份 `.asset`
#   · 物理 mod 完全不碰
_CORE_PRESERVE = (
    "descriptor.mod",
    "gfx/portraits/environments/zz_moe_environments.txt",
    "gfx/portraits/jomini_environment.txt",
    "gui/hud_sidebars.gui",
    "gui/multiplayer_lobby.gui",
)
_core_root = O.root(O.CORE)
_preserved_core = {
    rel: (_core_root / rel).read_bytes()
    for rel in _CORE_PRESERVE if (_core_root / rel).is_file()
}
O.reset(O.CORE)
for _rel, _data in _preserved_core.items():
    O.write_bytes(O.CORE, _rel, _data)
print(f"  已清空并重建 [{O.CORE}]，保护后续生成器产物 "
      f"{len(_preserved_core)}/{len(_CORE_PRESERVE)} 项")


# ================================================================ 描述符
# supported_version 用通配符覆盖 1.18.2 及后续补丁
DESCRIPTOR = f'''version="0.1.0"
tags={{
	"Portraits"
	"Graphics"
}}
name="Moe Florence PoC"
supported_version="1.18.*"
'''
# descriptor 现在由 finalize 步骤按 mod 逐个写（7 个 mod 各一份 +
# 外层 .mod）。这里再写一份会落进核心并被覆盖，纯属噪声。

# ================================================================ 模型资产
# 拷贝 PDX 产物
# **三类资源的路径基准不同（2026-08-26 实机确认）**：
#     .anim / .mesh  相对 `.asset` 所在目录   flo/anims/x.anim
#     .dds           相对 **.mesh 所在目录**  ../tex/x.dds
# 贴图按 asset 基准写会被拼成 flo/models/flo/tex/x.dds 而找不到。
#
# `.asset` 所在目录。资源本身在它下面按 角色/类型 分子目录 ——
# 路径规则集中在 tools/lib/moe_paths.py，改结构只改那一个文件。
#
# **本脚本只产出芙洛伦的资源**（_mesh_specs 是 moe_head/torso/clothes），
# 所以下面的 mesh 与贴图一律写 flo/；moe_invisible* 是两角色共用，走 shared/。
ASSET_DIR = MP.ASSET_REL
# **一律从 moe_paths 推，别写死。** 目录重组后 `.mesh` 共用一个 `models/`
# （靠文件名前缀区分），贴图在 `models/<短名>_tex/` —— 贴图路径是相对
# **.mesh 所在目录**解析的，所以必须挨着 mesh 放。
# 这三个常量此前写的是重组前的 `flo/models`、`flo/tex`、`shared/tex`，
# 结果拷第一张贴图就 WinError 3（父目录不存在），而 rmtree 已经跑完 ——
# mod 停在半成品状态。与 rank_anim_motion / graft_all_head_anims 是同一批残留。
FLO_MODELS = MP.rel_of(MP.models_dir("flo"))
FLO_MODELS = f"{ASSET_DIR}/{FLO_MODELS}"        # .mesh 落地
FLO_TEX = MP.rel_of(MP.tex_dir("flo"))
FLO_TEX = f"{ASSET_DIR}/{FLO_TEX}"              # .dds / .png 落地
SHARED_TEX = MP.rel_of(MP.SHARED_TEX)
SHARED_TEX = f"{ASSET_DIR}/{SHARED_TEX}"        # 共用贴图落地
# 芙洛伦 mesh 的来源：**与另三个角色一样，走她自己的烘焙产物目录**
# `work/moe_anim_bake/`（2026-08-29 起，200 骨 = 198 + 2 根道具挂点骨）。
#
# 这里曾经写死 `work/flo_mesh_shipped/`（189 骨 / fx 22），因为当时
# moe_anim_bake 里是 198 骨的实验产物而**没有配套动画**，2026-08-27 从它拷贝
# 的构建进游戏就报
#   Animation "moe_head_stayback_loop" does not have the same amount of joints
#   as the mesh it is trying to animate. ( Animation 189, Mesh 198 )
# 加上 moe_torso_mesh 7380 次 + moe_head_mesh 6864 次 PlayAnimation failed。
#
# 2026-08-29 重烘之后那个理由不再成立：新产物是 200 骨（双侧合并 + `_rev`
# 反向骨 + 道具挂点骨），并且她的动画已按新骨表全量重生成。
# 道具挂点骨是必需的 —— 没有它 `翻书` 之类动作播了却没有书，日志刷
# 432 条 invalid node。
#
# 还是不能用 work/florence_poc/：那是 167 骨（连 fx 代理骨都还没加）。
#
# **约束不变**：换这个源就必须同时重建她的全套动画，只换 mesh 会让 entity
# 完全不渲染。下面的骨数自检会把不一致报出来（抽样比对已有 .anim）。
BAKE = ROOT / "work" / "moe_anim_bake"

# 每个角色的 mesh + 模板 anim 从哪拷。**这一步以前是管线文档里的手工
# `cp work/moe_anim_bake/*.anim mod/.../`**，只对芙洛伦写了循环，另三个角色
# 靠手动放 —— 于是重烘之后很容易漏拷，而漏拷的表现是「骨数不匹配 → 那件
# mesh 上每条动画被拒播 → entity 完全不渲染」，本地渲图看不出来。
#
# 值是 (源目录, 该角色的文件名前缀)。源目录里的 `<前缀>head/torso.mesh` 与
# `<前缀>idle_calm.anim` 会被拷进它自己的模型子 mod。
# 目录不存在就跳过（不是所有角色都随时有新的烘焙产物）。
MESH_SRC = {c["ck"]: (X.bake_dir(c["ck"]), c["prefix"]) for c in ACTIVE}


def _bone_count(p):
    """数 .mesh 的骨数。**拷贝时就要报出来**，别等进游戏才发现不匹配。"""
    import pdx_parse as _PP
    s = set()
    for pp, _n, _k, _v in _PP.walk(_PP.parse(p.read_bytes())):
        if "/skeleton/" in pp:
            s.add(pp.split("/skeleton/")[1].split("[")[0])
    return len(s)


def _anim_bone_count(p):
    import pdx_parse as _PP
    return _PP.anim_info(p)['j']


def _copy_source_anim(task):
    source, destination = task
    # WinError 1224（"使用用户映射区域打开的文件"）/ 32（占用）是**瞬时**的：
    # 杀软或 Windows 索引服务会内存映射刚写完的 .anim 扫一遍，映射期间 CopyFile2 直接失败。
    # 2026-09-11 撞到一次，整个 [1/39] 退出、39 步全废、产物残缺。重试几次即过，
    # 比让用户手工重跑 14 分钟的构建划算；连续失败仍然抛，不静默跳过。
    # 2026-09-28 又撞一次：6 次共 ~8s 不够，拉到 12 次共 ~60s；失败时带上文件名。
    for _attempt in range(12):
        try:
            shutil.copy2(source, destination)
            break
        except OSError as _e:
            if getattr(_e, "winerror", None) not in (32, 33, 1224) or _attempt == 11:
                raise OSError(f"{_e}（{source} → {destination}）") from _e
            time.sleep(min(8.0, 0.5 * (_attempt + 1)))
    return destination.name, _anim_bone_count(destination)


# 每个角色的 mesh / 表情源拷贝与骨数合同互相独立，按线程并行（2026-10-01：92 人串行约 4 分钟）。
# 日志按角色收集，全部完成后按角色名顺序打印，与串行版输出一致。
def _copy_char_assets(_ck, _src_dir, _pfx):
    _lines = []
    _log = lambda *a: _lines.append(" ".join(str(x) for x in a))
    _mesh_counts = {}
    for _base in ("head.mesh", "torso.mesh", "clothes.mesh"):
        _name = f"{_pfx}{_base}"
        # 烘焙目录里的文件名不带角色前缀（导出的是 Blender 对象名
        # `moe_head`/`moe_torso`），落地时才加前缀。两种都试。
        _src = next((p for p in (_src_dir / _name, _src_dir / f"moe_{_base}")
                     if p.is_file()), None)
        if _src is None:
            # `clothes.mesh` **正常就不存在**（衣服已并入躯干，见
            # build_florence_poc.py 的「衣服并入躯干」一节），不当问题报。
            if _base == "clothes.mesh":
                continue
            # 其余缺失才是问题，而且**不静默回退到别的骨数版本**：骨数不等于
            # 动画骨数时引擎整段拒播且 entity 不渲染，本地渲图看不出来，
            # 静默换源等于把一整轮进游戏验收浪费掉。缺文件就说缺。
            _log(f"  !! {_ck} 缺 {_base}（找 {_src_dir.name}/）—— 不回退到别的"
                  f"骨数版本，要么补文件，要么见 README「仓库地图 → work/flo_mesh_shipped/」")
            continue
        _dst = MP.models_dir(_ck) / _name
        _dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_src, _dst)

        # **`.mesh` 内部对象名必须与 `.asset` 的 `pdxmesh name` 逐字相同。**
        # 导出器写的是 Blender 里的对象名（一律 `moe_head_mesh` / `moe_torso_mesh`，
        # 四个角色都从同一条管线出来），而 asset 声明的是带角色前缀的
        # `moe_lau_head_mesh`。不一致的症状是每个 shader 都报
        # `Failed to create material` + `pdxmesh is out of sync with its
        # meshsettings`，整个 entity 不渲染。
        #
        # 管线文档把这一步写成手工命令并注明「每次重新导出 mesh 都要重做 ——
        # 洛茜就是因为重建后漏做」。既然是「每次都要做」，就该在拷贝时自动做，
        # 而不是靠人记得。
        _want_obj = f"{_pfx}{_base[:-5]}_mesh"      # moe_lau_head_mesh
        _generic = f"moe_{_base[:-5]}_mesh"         # moe_head_mesh
        if _want_obj != _generic:
            import rename_mesh_object as _RMO
            _n = _RMO.rename(_dst, _generic, _want_obj)
            if _n:
                _log(f"    对象名 {_generic} → {_want_obj}（{_n} 处）")

        _mesh_counts[_name] = _bone_count(_dst)
        _log(f"  拷贝 {_name} ({_dst.stat().st_size:,} bytes, "
              f"{_mesh_counts[_name]} 骨) ← {_src_dir.name}/{_src.name}")

    # 烘焙产出的**全部**表情 `.anim` 一起拷（不只是模板那一条）。
    # **以前这是管线文档里的手工 `cp`**，漏了下一步的重定向会报
    # FileNotFoundError（那个还算显式），而拷了旧版本则是骨序对不上的静默错误。
    #
    # 为什么必须全拷：`graft_all_head_anims.py` 的表情源就是这几条
    # （`moe_angry` / `moe_happy` / `moe_sadness` / `moe_shock` / `moe_greet_bow`
    # / `moe_idle_calm`）。只拷 `idle_calm` 的话，重烘后另外 5 条留在 mod 里的
    # 还是旧骨数版本，嫁接产物**继承表情源的骨数**，于是 177 条 head anim 停在
    # 473 骨而 mesh 已是 475 —— 引擎按骨数不等整件拒播。2026-08-29 实测。
    #
    # 烘焙产物名是通名（`moe_angry.anim`），要换成该角色前缀。
    _tpl_name = f"{_pfx}idle_calm.anim"
    _torso_tpl_name = f"{_pfx}torso_idle_calm.anim"
    _anim_counts = {}
    _source_anim_names = set()
    _expr_n = 0
    _copy_tasks = []
    MP.anims_dir(_ck).mkdir(parents=True, exist_ok=True)
    for _es in sorted(_src_dir.glob("*.anim")):
        _en = (_pfx + _es.name[len("moe_"):]) if _es.name.startswith("moe_") else _es.name
        _ed = MP.anims_dir(_ck) / _en
        _copy_tasks.append((_es, _ed))
        _source_anim_names.add(_en)
        _expr_n += 1
    with ThreadPoolExecutor(max_workers=max(1, int(os.environ.get('MOE_BUILD_JOBS', '12')))) as _pool:
        _anim_counts.update(_pool.map(_copy_source_anim, _copy_tasks))
    if _expr_n:
        _log(f"  拷贝表情源 {_expr_n} 条（{_anim_counts.get(_tpl_name, '?')} 骨）"
              f" ← {_src_dir.name}/")

    # Royal Court 标准根只属于 torso：新合同允许 head=200、torso=202。
    # 因此必须按实际播放关系分组校验，不能再把三件 mesh 与所有源动画混成一个
    # `_counts` 后取众数。旧角色尚无独立 torso 模板时仍沿用 idle_calm，两个组
    # 自然会得到相同骨数，兼容原合同。
    _head_contract = {
        k: v for k, v in _mesh_counts.items() if k.endswith("head.mesh")
    }
    # 按名字分组：`{pfx}body_*` 与 torso 模板归 torso 合同，其余（表情源 + `{pfx}head_*`）归 head。
    # 2026-09-06 起烘焙目录里就有整套 body/head 动画（import_chars 直接写进去），
    # 不分组就是 24 人全报假警报。
    _body_pfx = f"{_pfx}body_"
    _head_contract.update({
        k: v for k, v in _anim_counts.items()
        if k != _torso_tpl_name and not k.startswith(_body_pfx)
    })
    _torso_contract = {
        k: v for k, v in _mesh_counts.items()
        if k.endswith("torso.mesh") or k.endswith("clothes.mesh")
    }
    _torso_anim_name = (_torso_tpl_name
                        if _torso_tpl_name in _anim_counts else _tpl_name)
    if _torso_anim_name in _anim_counts:
        _torso_contract[_torso_anim_name] = _anim_counts[_torso_anim_name]
    _torso_contract.update({
        k: v for k, v in _anim_counts.items() if k.startswith(_body_pfx)
    })

    def _check_joint_contract(label, counts):
        known = {k: v for k, v in counts.items() if v is not None}
        if len(set(known.values())) > 1:
            _log(f"  !! **{_ck} {label} 骨数不一致** {known} —— "
                  f"重烘之后必须重生成对应的 .anim，只换 mesh 会让该 entity "
                  f"完全不渲染")
        elif known:
            _log(f"  {_ck} {label} 合同: {next(iter(known.values()))} 骨")

    _check_joint_contract("head", _head_contract)
    _check_joint_contract("torso/clothes", _torso_contract)

    # **还要跟该角色已有的那几百条 .anim 比**。上面那条只比刚拷进来的几个文件，
    # 重烘之后它们内部一致、却与旧动画差 2 根骨 —— 而那正是最可能出的错：
    # 重烘了 mesh、忘了重生成动画。抽样比对（全量太慢），报出实际分布。
    _ad = MP.anims_dir(_ck)

    def _sample_existing(label, ref, candidates):
        if ref is None or not candidates:
            return
        _samp = candidates[::max(1, len(candidates) // 12)][:12]
        _dist = {}
        for _p in _samp:
            _n = _anim_bone_count(_p)
            _dist.setdefault(_n, []).append(_p.name)
        _off = {k: v for k, v in _dist.items() if k != ref}
        if _off:
            _log(f"  !! **{_ck} 已有的 {label} .anim 骨数与新 mesh 不符**："
                  f"mesh {ref} 骨，抽样 {len(_samp)}/{len(candidates)} 条里有 "
                  + "、".join(f"{k} 骨 ×{len(v)}" for k, v in sorted(_off.items()))
                  + f"（如 {next(iter(_off.values()))[0]}）\n"
                  f"     → 这个角色的动画必须重生成，否则她的 entity "
                  f"**完全不渲染**：\n"
                  f"       python tools/anim/build_all_anims.py --part torso "
                  f"--out-prefix {_pfx}\n"
                  f"       python tools/anim/graft_all_head_anims.py "
                  f"--out-prefix {_pfx}")

    if _ad.is_dir():
        _all_anims = sorted(_ad.glob("*.anim"))
        _head_ref = _mesh_counts.get(f"{_pfx}head.mesh")
        _torso_ref = _mesh_counts.get(f"{_pfx}torso.mesh")
        _head_existing = [p for p in _all_anims
                          if p.name.startswith(f"{_pfx}head_")]
        # 烘焙出的 head 表情源没有 `head_` 前缀，不能拿去和 torso 骨数比较。
        _torso_existing = [p for p in _all_anims
                           if not p.name.startswith(f"{_pfx}head_")
                           and (p.name not in _source_anim_names
                                or p.name == _torso_tpl_name)]
        _sample_existing("head", _head_ref, _head_existing)
        _sample_existing("torso", _torso_ref, _torso_existing)
    return _lines


with ThreadPoolExecutor(max_workers=max(1, int(os.environ.get('MOE_BUILD_JOBS', '12')))) as _cpool:
    _jobs = [(_ck, _sd, _pf) for _ck, (_sd, _pf) in sorted(MESH_SRC.items()) if _sd.is_dir()]
    for _lines in _cpool.map(lambda t: _copy_char_assets(*t), _jobs):
        for _l in _lines:
            print(_l)

# 动画：bake 出的表情/动作 + retarget 出的身体动作，都已在 mod 目录里。
#
# **不要再拷 work/florence_poc/moe_idle.anim。** 它是 bake 之前的产物，只有
# **167 骨**（缺 22 根 fx 代理骨），而三件 mesh 都是 189 骨。骨数不匹配的
# 后果不是「动作难看」而是引擎整段拒播：
#     [pdxmeshobject.cpp:870] PlayAnimation failed for mesh "moe_torso_mesh"
# 而 torso 的 default_state = idle 正好引用它 → 躯干与衣服**完全不渲染**，
# 画面上只剩一个头。头部侥幸没坏，是因为 register_body_anims.py 把 head 侧的
# idle 重指向了 headb_idle_calm，torso 侧没跟着改。踩过一次，见 README。
#
# 现在 idle / idle_1_base 一律指向 189 骨的 moe_idle_calm.anim（下面 asset_text）。
STALE_167 = MP.anims_dir("flo") / "moe_idle.anim"
if STALE_167.is_file():
    STALE_167.unlink()
    print("  删除遗留的 moe_idle.anim（167 骨，会让 PlayAnimation 整段失败）")

# 贴图：每个就绪角色的 diffuse 落位 + properties / normal 生成，全部交给
# tools/textures/build_char_textures.py（按登记表找源、按命名规则配对 RMO/_P/法线、
# 共用图只写一份进该游戏的 shared_tex/）。以前这里只拷芙洛伦的 diffuse，properties
# 脚本又写死她的源目录，终末地三人另有一份专用脚本 —— 三处常量各自漂移。
# 必须在 .asset 生成之前跑完 —— asset_text() 按「文件是否存在」决定要不要写
# texture_specular / texture_normal；缺图不是「用默认值」而是采样成白图（金属反光 +
# 横向法线，见 build_moe_properties_maps.py 顶部注释）。
print("\n贴图落位 + properties / normal:")
_ct = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "textures" / "build_char_textures.py"), "--all"],
    capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
_ct_enc = getattr(sys.stdout, "encoding", None) or "utf-8"
for _line in (_ct.stdout or "").splitlines():
    if _line.startswith(("===", "  diffuse", "  !!", "  跳过")):
        print("  " + _line.encode(_ct_enc, "replace").decode(_ct_enc, "replace"))
if _ct.returncode != 0:
    print(_ct.stderr[-1500:] if _ct.stderr else "")
    raise SystemExit("**贴图生成失败**（见上）")
tex_copied = sorted(l.strip() for l in (_ct.stdout or "").splitlines() if l.startswith("=== "))
tex_needed = set(tex_copied)

# ================================================================ .asset
# 动画集 + pdxmesh + entity 三段，结构对齐原版 female_head.asset
def anim_set_of(tkey, part=None):
    """该角色的 skeletal_animation_set 名字。

    **每个角色必须有自己的集合。** 原先三个角色共用一个 `moe_common`，
    而 `register_all_anims.py` 是**整体覆盖**目标集合的 —— 依次为洛茜、
    洁尔佩塔注册之后，集合里只剩最后一个角色的 animation 条目，
    前两个角色的 state 全都引用到不存在的 id（实测：
    `moe_lau_body_*` state 726 行、animation 0 条）。
    引擎对找不到的 animation 是整段拒播 + entity 静默不渲染。
    """
    base = ANIM_SET if tkey == "moe_flo" else f"{ANIM_SET}_{tkey[len('moe_'):]}"
    _pfx = _mesh_of(tkey, "torso")[1][:-len("torso.mesh")]
    _ck = MP.char_of(_pfx) or "flo"
    split = (MP.anims_dir(_ck) / f"{_pfx}torso_idle_calm.anim").is_file()
    return f"{base}_torso" if split and part == "torso" else base


def _strip_dup(name):
    """剥掉 Blender 的重复 datablock 后缀 `.001`。

    源文件里同一张图被引用两次时，Blender 把第二份命名为 `x.png.001`，
    report 原样记下来。不剥的后果：diffuse 查不到 → 那一层渲成白图
    （洁尔佩塔的头发），而 `_properties` / `_normal` 名字会拼成
    `x.png_properties.dds` —— 两个错叠在一起，只看头发白容易只修一半。
    """
    return re.sub(r"\.\d{3}$", "", name or "")


def tex_stem(name):
    """贴图基名：剥重复后缀，再剥扩展名。`x.png.001` → `x`。"""
    return Path(_strip_dup(name)).stem


def tex_ref(ck, filename):
    """贴图在 .asset 里该写的引用；找不到文件返回 None。

    **必须是裸文件名，不能带任何目录**（2026-08-28 实机定案）。
    引擎在启动时对 `gfx/models` 建一张**按基名索引的平表**：
        [D][pdxassetutil.cpp:71]: ThreadedInitTextureLookup: gfx/models
        [D][pdxassetutil.cpp:79]: ThreadedInitTextureLookup found 21203 files
    查表的键是基名，所以任何带 `/` 的引用都查不到，报
        [E][pdxassetutil.cpp:1050]: Failed to find texture 'per_tex/x.dds'
    原版 18953 条 texture_* 引用**全部是裸基名，带斜杠的 0 条** —— 这是
    引擎的约定，不是可选风格。

    **这条错误极难定位，因为画面看起来是对的**：diffuse 由 `.mesh` 二进制
    内嵌的裸名加载（那条路走平表，正常命中），只有 .asset 里 meshsettings
    覆盖的 `texture_specular`/`texture_normal` 静默失效。表现是「贴图正常
    但没有高光、法线不对」，本地渲图与截图都看不出来。
    我曾经把这 63 条报错当成「基线噪声」放过一次 —— 别再放过。

    我此前推的两条约束是错的，记在这里免得再走回去：
      - 「基准是 .mesh 所在目录」：不存在基准目录这回事，是平表查基名。
      - 「所以贴图必须在 mesh 目录之下、写 `<角色>_tex/x.dds`」：由上一条
        推出，同样错。`..` 被 VFS 拒绝那件事是真的，但正确解法是去掉目录，
        不是改成相对子目录。
    贴图放在哪个子目录都无所谓（只要在 `gfx/models` 下），**但基名必须全局
    唯一** —— 平表按基名索引，撞名会互相顶掉。当前 102 个基名全唯一、与
    原版零冲突（判据见 tools/verify/verify_asset.py）。

    顺带统一扩展名：report 里记的是源 `.png` 名，磁盘上是转好的 `.dds`。
    这里按实际存在的文件挑，别让 `.png` 引用漏出去。
    """
    if not filename:
        return None
    filename = tex_stem(filename) + Path(_strip_dup(filename)).suffix
    stem = Path(filename).stem
    # **`.png` 也算存在**：asset 在 DDS 转换之前写，新目录（首次构建的角色、刚搬进
    # shared_tex/ 的共用图）此时只有 .png；只查 .dds 会漏写 properties/normal 引用
    # （2026-09-06 回归：extra 搬到 gfl2 shared_tex 后 4 条「缺 properties/normal」）。
    # 返回值一律是 .dds 名 —— 转换脚本随后会把 .png 转掉。
    cands = [f"{stem}.dds", f"{stem}.png", filename]
    # 三处都要找：角色自己的贴图目录、跨角色共用的（表情图集，在 endfield
    # 包）、渲染工具贴图（全透明三件套，在 shader 包）。
    # 漏掉最后一处的后果是 8 个 `no_render` 层拿不到贴图引用 → **照常渲染
    # 出来**，其中 Eyes+ 就是盖住虹膜的那层实心白高光。
    for base in (MP.tex_dir(ck), *MP.shared_tex_dirs(), MP.util_tex_dir()):
        for c in cands:
            if (base / c).is_file():
                return f"{stem}.dds"    # **裸基名**，不带目录；统一 .dds
    # 兜底：按文件名归属路由（MP.find 与 dest 同一套前缀规则）。两个角色引用同名同内容的图
    # 时（科谢尼娅的躯干挂着莫辛纳甘的 plush，两包源 md5 相同），写入按前缀只落进拥有者的
    # 目录 —— 这是正确去重（重名 = Duplicate texture），但引用方按自己目录查就漏了：
    # meshsettings 不写 texture_diffuse，引擎退回 mesh 内嵌的 .png 名 → 「Could not find
    # texture ...plush_dt.png」（2026-09-06 实机）。平表按基名索引，放在谁的目录都能引用。
    for c in cands:
        try:
            q = MP.find(c, missing_ok=True)
        except ValueError:
            continue
        if q is not None and pathlib.Path(q).is_file():
            return f"{stem}.dds"
    return None


def asset_text(only_ck=None):
    """`.asset` 文本。`only_ck` 给定时只出这些角色的块（可给单个或集合）。

    ## 为什么必须能按角色切分

    产物直写各子 mod 后，模型按来源游戏分散：flo 在 gfl2 包，lau/per/eld
    在 endfield 包。而 `.asset` 原先是**一份共用文件**，路由整体归给 gfl2 ——
    于是只装 endfield 模型包（不装 gfl2）时，mesh 全在但没有任何 `.asset`
    描述它们，**什么都不渲染且日志不报错**。这直接违背第 4 项
    「子 mod 之间自由组合」的目标。

    每个模型子 mod 出自己那份 `.asset`，块之间本来就是按角色独立的
    （每角色 2 个 pdxmesh + 2 个 entity + 1 个动画集，名字全带角色前缀），
    所以切分是无损的。
    """
    # **按 mod 分组，不是按单个角色**：lau 与 per 同属 endfield 包，
    # 一个角色写一次的话第二次会把第一次覆盖掉（实测 endfield 的 asset
    # 只剩 per，lau 的 mesh 全在但没人描述）。
    _want = ({only_ck} if isinstance(only_ck, str)
             else set(only_ck) if only_ck else None)
    _types = ([tk for tk in TYPES
               if (MP.char_of(_mesh_of(tk, "torso")[1]
                              [:-len("torso.mesh")]) or "flo") in _want]
              if _want else list(TYPES))
    lines = []
    # 通常一个角色一个动画集；但 Royal Court 标准根只存在于 torso 时，head 与
    # torso 骨数不同，必须再按部件拆成两套 reference skeleton。只拆 mesh/anim
    # 而仍共用一套 set 会让 200 骨 head 导入以 202 骨 torso 为 reference 的 idle，
    # 实机表现就是主菜单头、帽子、身体再次飞离。
    for _tk in _types:
        _head_mesh = _mesh_of(_tk, "head")[0]
        _torso_mesh = _mesh_of(_tk, "torso")[0]
        # idle 必须指向**与该角色 mesh 骨数一致**的 idle_calm。曾经指向
        # 167 骨的 moe_idle.anim（bake 前产物），引擎报 PlayAnimation failed
        # 且整个躯干不渲染 —— 骨数不一致是**整段拒播**，不是降级播放。
        _pfx = _mesh_of(_tk, "torso")[1][:-len("torso.mesh")]
        _ck = MP.char_of(_pfx) or "flo"
        # head 的中性表情模板与 torso 可以有不同骨数。新王座根契约只给
        # torso 增加 ground_joint/body_root，因此优先用独立 torso 模板；
        # 尚未重烘的角色继续兼容共用 idle_calm。
        _torso_idle_name = f"{_pfx}torso_idle_calm.anim"
        _split = (MP.anims_dir(_ck) / _torso_idle_name).is_file()
        _head_idle = f"{_ck}/anims/{_pfx}idle_calm.anim"
        _torso_idle = (f"{_ck}/anims/{_torso_idle_name}"
                       if _split else _head_idle)
        _sets = ([(anim_set_of(_tk, "head"), _head_mesh, _head_idle),
                  (anim_set_of(_tk, "torso"), _torso_mesh, _torso_idle)]
                 if _split else
                 [(anim_set_of(_tk), _torso_mesh, _head_idle)])
        for _set_name, _ref_mesh, _idle in _sets:
            lines.append(f'skeletal_animation_set = {{')
            lines.append(f'\tname = "{_set_name}"')
            lines.append(f'\treference_skeleton = "{_ref_mesh}"')
            lines.append(f'\tanimation = {{ id = "idle" \t\t\ttype = "{_idle}" }}')
            lines.append(f'\tanimation = {{ id = "idle_1_base" \ttype = "{_idle}" }}')
            lines.append(f'}}')
            lines.append('')

    # 衣服已并入躯干（见 build_florence_poc.py 的「衣服并入躯干」一节），
    # 正常情况下 moe_clothes.mesh 不再产出。这里按**文件是否存在**决定写不写
    # 它的 pdxmesh 块 —— 写了却没文件，引擎报 mesh 加载失败。
    # **按 TYPES 循环出每个角色的 mesh**（README 第 153 行记的就是这个设计，
    # 但实现一直只写死芙洛伦两条）。后果是本脚本整体重写 asset 时，
    # 会把其余角色的 pdxmesh / entity 块**冲掉** —— 自洛茜加入后没人重跑过
    # 生成器，所以一直没暴露；重跑时 register_all_anims 立刻报「找不到 entity」。
    #
    # 第四元素 `info` 是**该角色自己 report 的 parts**：meshsettings 必须按
    # 自己的 submesh 生成，缺一条整层不渲染而日志只报一行。
    # 第五元素是**角色短名**：贴图目录按角色分（models/<ck>_tex/），
    # 写贴图引用时必须知道是谁的，不能一律当芙洛伦。
    _mesh_specs = []
    for _tk in _types:
        _pt = _parts_of(_tk)
        _ck = MP.char_of(_mesh_of(_tk, "torso")[1][:-len("torso.mesh")]) or "flo"
        for _part in ("head", "torso"):
            _mn, _fn = _mesh_of(_tk, _part)
            _mesh_specs.append((_mn, _fn, f"moe_{_part}", _pt,
                                anim_set_of(_tk, _part), _ck))
    if ((_want is None or "flo" in _want)
            and (MP.models_dir("flo") / "moe_clothes.mesh").is_file()):
        _mesh_specs.append((MESH_CLOTHES, "moe_clothes.mesh",
                            "moe_clothes", parts,
                            anim_set_of("moe_flo", "torso"), "flo"))

    for mesh_name, file_name, part_key, _pt, _set_name, _ck in _mesh_specs:
        info = _pt.get(part_key, {})
        lines.append(f'# {part_key}: 顶点={info.get("vertices")} 面={info.get("polygons")}')
        lines.append(f'pdxmesh = {{')
        lines.append(f'\tname = "{mesh_name}"')
        # 路径相对 **.asset 所在目录**解析。目录重组后 mesh 全在 `models/`
        # （靠文件名前缀区分角色），此前写的 `flo/models/` 已不存在。
        # 注意贴图路径是相对 **.mesh 所在目录**解析的，两者基准不同。
        lines.append(f'\tfile = "models/{file_name}"')
        # head 与 torso 必须同一个 scale，见 PORTRAIT_SCALE 的注释
        lines.append(f'\tscale = {PORTRAIT_SCALE}')
        # 不写 streaming = Never，走 1.20 默认流式。2026-10-01 用户定案关掉：
        # Never 让 56 件带骨架 mesh 与整套动画集在启动时全量常驻，加 Never 之后原版立绘全空。
        # 流式的代价是 entity 解析时报「does not have an animated mesh」（见记忆
        # ck3-120-streamed-mesh-no-animated-mesh-error），用户已接受。
        lines.append('')
        # **import 自己的动画集**，不是共用那个。共用会让最后一次注册覆盖掉
        # 前面角色的 animation 条目（见 anim_set_of 的文档字符串）。
        lines.append(f'\timport = {{')
        lines.append(f'\t\ttype = skeletal_animation_set')
        lines.append(f'\t\tname = "{_set_name}"')
        lines.append(f'\t}}')
        lines.append('')

        # meshsettings 是必需的：mesh 里只存 shader 名，
        # shader_file 必须在这里指定，否则引擎默认去 gfx/FX/pdxmesh.shader 找，
        # 报 "Failed to create material with shader portrait_skin (in gfx/FX/pdxmesh.shader)"。
        # 每个 submesh 一条，index 用实际材质槽编号（不连续）。
        subs = info.get("submeshes") or []
        if not subs:
            lines.append('\t# !! 报告里没有 submesh 信息，无法生成 meshsettings')
        for sm in subs:
            # no_render: 该层照常注册 meshsettings，但贴全透明贴图 +
            # portrait_hair_alpha（PS_hair 里有 clip(Diffuse.a - 1e-5)），
            # 于是每个片元都被丢弃，一个像素都画不出来。
            #
            # 为什么不能干脆跳过这条 meshsettings（我先这么写，实测报错）：
            #   [pdxassetutil.cpp:897] Failed to create material with shader
            #   portrait_attachment_alpha_to_coverage (in gfx/FX/pdxmesh.shader)
            # shader **名字写在 .mesh 二进制里**，引擎会为每个材质槽建材质；
            # meshsettings 的作用是提供 shader_file。缺了它引擎去默认的
            # gfx/FX/pdxmesh.shader 找 portrait_* shader，找不到就报错。
            # 所以「不渲染」只能靠让片元被丢弃，不能靠不注册。
            #
            # 目前只有 Eyes+（MMD 眼睛高光层，CK3 无 additive 混合，
            # 任何 shader 都会把柔光穹顶画成盖住虹膜的实心白）。
            # 判定与理由见 tools/model/build_florence_poc.py 的 NO_RENDER_MATS。
            # 以**合同**为准，不读 report 里的 no_render 快照：合同改了（2026-09-06 Eyes+ 放回渲染）
            # 不必重建 24 个 mesh；材质级渲染决策必须落在合同里（[[ck3-alpha-to-coverage-kills-gradients]]）。
            if MC.is_no_render(sm["material"]):
                lines.append(f'\tmeshsettings = {{')
                lines.append(f'\t\tname = "{mesh_name}"')
                lines.append(f'\t\tindex = {sm["index"]}')
                lines.append(f'\t\t# 刻意不可见，见 build_florence_poc.py NO_RENDER_MATS')
                for _f, _t in ((INVISIBLE_TEX, "diffuse"),
                               (INVISIBLE_TEX_PROPS, "specular"),
                               (INVISIBLE_TEX_NORMAL, "normal")):
                    _r = tex_ref(_ck, _f)
                    if _r:
                        lines.append(f'\t\ttexture_{_t} = "{_r}"')
                    else:
                        print(f"  !! 缺全透明贴图 {_f} —— 该层会照常渲染出来")
                lines.append(f'\t\tshader = "{moe_shader_name(MC.pdx_shader(sm["material"]))}"')
                lines.append(f'\t\tshader_file = "{PORTRAIT_SHADER_FILE}"')
                lines.append(f'\t}}\t# {sm["material"]} '
                             f'({sm.get("polygons")} 面) 刻意不可见')
                continue
            diff = sm.get("diffuse")
            shader = MC.pdx_shader(sm["material"], [diff] if diff else [])
            lines.append(f'\tmeshsettings = {{')
            lines.append(f'\t\tname = "{mesh_name}"')
            lines.append(f'\t\tindex = {sm["index"]}')
            if diff:
                _dr = tex_ref(_ck, diff)
                if _dr:
                    lines.append(f'\t\ttexture_diffuse = "{_dr}"')
                else:
                    print(f"  !! 缺 diffuse 图: {diff}（{_ck}）—— 该层会是白图")
                # 槽位 1 (texture_specular = PropertiesMap) 与
                # 槽位 2 (texture_normal = NormalMap) 必须显式给图。
                # 不给不是「用中性值」，而是采样成不透明白色：
                # metalness=1 spec=1 → 皮肤布料被当抛光金属渲染（奇怪的反光），
                # 法线白图解包成横向切空间法线 → 全身光照方向错误。
                # 这两张由 tools/textures/build_moe_properties_maps.py 从源 RMO/normal 重打包。
                # **用 tex_stem 而不是 Path(diff).stem** —— 后者对
                # `x.png.001` 只剥掉 `.001`，剩下 `x.png`，于是伴生图名
                # 拼成 `x.png_properties.dds`，永远查不到。
                stem = tex_stem(diff)
                # 门控与路径都交给 tex_ref：它按**该角色**的贴图目录查、
                # 按实际存在的扩展名挑。曾经这里写死查 FLO_TEX 且只找 .png，
                # 于是洛茜/洁尔佩塔/艾尔黛拉三个角色**一张 properties 和
                # normal 都写不出来** —— 皮肤布料被当抛光金属渲染、
                # 法线白图解包成横向切空间法线，全身光照方向错。
                props = f"{stem}_properties.dds"
                _pr = tex_ref(_ck, props)
                if _pr:
                    lines.append(f'\t\ttexture_specular = "{_pr}"')
                else:
                    print(f"  !! 缺 properties 图: {props}（该 submesh 会有金属反光）")
                nrm = f"{stem}_normal.dds"
                _nr = tex_ref(_ck, nrm)
                if _nr:
                    lines.append(f'\t\ttexture_normal = "{_nr}"')
                else:
                    # 原先这一路**连警告都没有**，法线缺失是静默的：
                    # 白图会解包成横向切空间法线，全身光照方向错。
                    print(f"  !! 缺 normal 图: {nrm}（该 submesh 光照方向会错）")
            lines.append(f'\t\tshader = "{moe_shader_name(shader)}"')
            lines.append(f'\t\tshader_file = "{PORTRAIT_SHADER_FILE}"')
            lines.append(f'\t}}\t# {sm["material"]} ({sm.get("polygons")} 面)')
        lines.append(f'}}')
        lines.append('')

    # entity 的两个必需项，都是实测撞出来的：
    #
    # 1. default_state / state 必须写。
    #    原版 head_basic_entity_female 确实没写，但它有 40 个 attribute
    #    （捏脸滑条，每个背后一个 additive_animation）。引擎的规则是
    #    「有 attribute 的动画 mesh 可以省 default_state」，我们没有 attribute，
    #    省掉就报：
    #      Missing default_state for entity "moe_head_entity" with animated
    #      mesh "moe_head_mesh" and no attributes
    #    末尾那句 and no attributes 就是判据。等以后烘出 additive_animation
    #    再考虑对齐原版写法。
    #
    # 2. portrait_decal 指明这个 entity 是身体哪一部分（原版 head 写
    #    body_part = head、torso 写 body_part = torso）。
    # 每个角色 adult/child 各有 head、torso entity，共四块。Child 实体复用同
    # 一份骨架兼容网格，只在 entity 层缩小整套模型；`register_all_anims.py`
    # 会把完整 state 表分别注入两套实体，所以必须在其前写出骨架块。
    _ent_specs = []
    for _tk in _types:
        for _part in ("head", "torso"):
            _ent = TYPES[_tk][2 if _part == "head" else 3]
            _mesh_name = _mesh_of(_tk, _part)[0]
            _ent_specs.append((_ent, _mesh_name, _part, None))
            _ent_specs.append((_ent + CHILD_ENTITY_SUFFIX, _mesh_name, _part,
                               CHILD_PORTRAIT_SCALE))
    for ent, mesh_name, body_part, entity_scale in _ent_specs:
        lines.append(f'entity = {{')
        lines.append(f'\tname = "{ent}"')
        lines.append(f'\tpdxmesh = "{mesh_name}"')
        if entity_scale is not None:
            lines.append(f'\tscale = {entity_scale}')
        lines.append(f'\tdefault_state = "idle"')
        lines.append(f'\tstate = {{ name = "idle" \t\tstate_time = 2.0 \tanimation = "idle" \tlooping = yes }}')
        lines.append(f'\tgame_data = {{')
        lines.append(f'\t\tportrait_entity_user_data = {{')
        # color mask 必须关掉（区间钉死在 0），否则二次元贴图会被基因肤色压暗。
        #
        # portrait.shader 的 PS_skin / PS_eye / PS_attachment 都这么干：
        #     float ColorMaskStrength = Diffuse.a;
        #     Diffuse.rgb = GetColorMaskColorBLend( Diffuse.rgb,
        #                       vPaletteColorSkin.rgb, InstanceIndex, ColorMaskStrength );
        # 而 GetColorMaskColorBLend 里：
        #     LerpTarget = RemapToInterval( ColorMaskStrength, Interval.x, Interval.y );
        #     return lerp( Diffuse, Diffuse * PaletteColor, LerpTarget );
        #
        # 原版贴图把 alpha 当「这里有多少比例交给基因肤色」的遮罩来画，
        # 所以皮肤区 alpha 高、非皮肤区 alpha 低。我们的二次元贴图是成品
        # 立绘，alpha 实测几乎全是 255（脸 mean 255、身体 255、头发 255），
        # 于是 ColorMaskStrength≈1 → 整张图变成 Diffuse * 基因肤色。
        # 基因肤色不是白色，乘上去就是整体压暗发灰 —— 就是「脸很暗很脏」。
        #
        # 把区间钉成 { 0.0 0.0 } 后 LerpTarget 恒为 0，lerp 取回原始 Diffuse，
        # 贴图原色 100% 保留。代价是失去基因驱动的肤色变化 ——
        # 对固定立绘的二次元角色来说这正是想要的。
        # 将来若要做「多套发色/肤色」，正确做法是重画 alpha 遮罩，
        # 而不是放开这个区间。
        lines.append(f'\t\t\tcolor_mask_remap_interval = {{')
        lines.append(f'\t\t\t\tinterval = {{ 0.0 0.0 }}')
        lines.append(f'\t\t\t}}')
        lines.append(f'\t\t\tportrait_decal = {{')
        lines.append(f'\t\t\t\tbody_part = {body_part}')
        lines.append(f'\t\t\t}}')
        lines.append(f'\t\t}}')
        lines.append(f'\t}}')
        lines.append(f'}}')
        lines.append('')

    # ---- 衣服 entity
    # 姿势由 accessory 的 shared_pose_entity = torso 继承（见
    # gfx/portraits/accessories/moe_clothes/moe_clothes.txt）。
    #
    # **但仍然要写 default_state + state。** 我原先照抄原版 f_clothes 的结构
    # （无 default_state / 无 state），断言「共用姿势的 entity 不需要状态机」，
    # 实测是错的：
    #     [pdx_entity.cpp:3321] Missing default_state for entity
    #     "moe_clothes_entity" with animated mesh "moe_clothes_mesh"
    #     and no attributes
    # 末尾 `and no attributes` 才是真正的豁免条件。原版 f_clothes 的 entity
    # 带 **9 个 attribute**（bs_body_fat_1 / bs_body_pregnant_1 等 blend_shape
    # 捏体型滑条），靠这个豁免；我们一个 attribute 都没有，所以豁免不了。
    # 与 head/torso 的 default_state 是同一条引擎规则，不是衣服的特例。
    if (MP.models_dir("flo") / "moe_clothes.mesh").is_file():
        lines.append(f'entity = {{')
        lines.append(f'\tname = "{ENTITY_CLOTHES}"')
        lines.append(f'\tpdxmesh = "{MESH_CLOTHES}"')
        lines.append(f'\tdefault_state = "idle"')
        lines.append(f'\tstate = {{ name = "idle" \t\tstate_time = 2.0 \tanimation = "idle" \tlooping = yes }}')
        lines.append(f'\tgame_data = {{')
        lines.append(f'\t\tportrait_entity_user_data = {{')
        # 同样钉死 color mask：衣服贴图也是成品立绘，alpha≈255，
        # 不钉的话会被基因肤色乘暗（与 head/torso 同一个坑）。
        lines.append(f'\t\t\tcolor_mask_remap_interval = {{')
        lines.append(f'\t\t\t\tinterval = {{ 0.0 0.0 }}')
        lines.append(f'\t\t\t}}')
        lines.append(f'\t\t}}')
        lines.append(f'\t}}')
        lines.append(f'}}')
        lines.append('')
    return "\n".join(lines)



# ================================================================ 方案 C：独立肖像 group
# 完整机制见 docs/PORTRAIT_GROUP_MECHANISM.md，四个环节全部实测验证过
# （隔离环境 error.log 除无关旧存档外 0 错误）。
#
# 核心结论：
#   1. portrait type 名是全局唯一的，不是 group 内局部名。
#      复用 male/female/boy/girl 会把它们从 human 抢走，导致 human 变空 group，
#      引擎报 "Type male is not part of the human group" 上千次。
#   2. 每个 group 必须在 gfx/portraits/customization_rules/ 注册，规则名 = group 名。
#      引擎双向校验：
#        "Missing portrait customization rule entry for portrait group {}"
#        "Customization rule {} does not exist as a portrait group"
#   3. gene 归属靠容器内首行 portrait_group = <group>。
#      文件顶层写、gene 内写都被静默忽略；用 group 名作容器键、容器名加前缀
#      都报 Unexpected token。gene 不能跨 group 共享。
#   4. 角色指派入口是 dna_data 的 portrait_info 内 type = <portrait type 名>。
#      portrait_type / portrait_group / portrait_customization 全报 Unexpected token。
#
# group 间命名空间完全隔离：moe 的 gene 可与原版同名，不报 already exist，
# 原版角色也不受影响。
#
# ---------------------------------------------------------------------------
# **一个角色一个 group**（2026-08-27 定案，替换此前的「三个 type 共用一个
# group」）。根因：**portrait type 是按 sex + age 实时重算的，不能固化在
# 角色身上**。证据两条：
#   1. 原版 type 名就是 male/female/boy/girl —— 那是 sex×age 四个互斥槽位，
#      不是自由标识符。男童满 18 岁必须自动变成 male，所以引擎每次渲染都要
#      按当前 sex/age 重挑。
#   2. exe: "Minimum age is greater than maximum age in portrait type {}"
#      —— age 是 type 的选择键。
# DNA 里的 type 只用于**定位 group + 解析 gene 集**。每角色一个 group 避免
# 不同模型互抢；group 内 adult 与 girl/boy 用互斥年龄范围，选择不同尺寸实体。
#
# 症状：选洛茜或洁尔佩塔进游戏，立绘全是芙洛伦（书签预览却是对的，因为
# bookmark_portraits 直接给 type=，不走这条重算路径）。
#
# 这不是配置错误，是机制限制：type 级没有 trigger 字段，attach 只在 group 级
# （所以同 group 内所有 type 必须共用骨架命名）。唯一修法是拆 group。
GROUPS = {c["type"]: c["group"] for c in ACTIVE}
# 兼容旧引用：文件名前缀、日志文本仍用这个词根。
GROUP = "moe"
# (sex, 年龄条件, head 实体, torso 实体)
#
# **模型按性别分池**：女性与男性各自只会随机到同一性别的 MMD portrait type；
# 每个 portrait type 仍有自己的 head/torso entity 和完整动画集。
#
# **不能在本 group 内让男性回落到原版实体**（2026-08-26 实机否证）。试过补
# moe_male/moe_boy 指向 head_basic_entity_male + male_body_entity，并把原版
# 那三组 joint_attachment 与 bn_m_root 并列。结果 error.log 刷了 7442 条
# 并持续增长：
#     male_body_entity has no attach point named bn_m_root
# 引擎**不会**静默丢弃 attach 里不存在的骨 —— 那条规则
#（ck3-anim-bone-must-exist-in-mesh）只在 .anim 语境成立，我错误推广了它。
#
# 字段集实测（原版 00_human_types.txt）：attach 只存在于 **group 级**，
# type 级只有 head/torso/sex/minimum_age，没有 per-type attach。所以
# **一个 group 内所有 type 必须共用同一套骨架命名**。这条限制仍适用于
#「把原版 male_body_entity 塞进 Moe group」的旧方案。
# 终末地男模使用自己的 male type、head/torso entity 和本系列模型骨架；模型池
# 候选事件同时检查性别，因此 female 与 male ethnicity 不会交叉指派。
#
# 原版 ethnicity 没有性别筛选字段，所以性别分池必须由角色 effect 与候选事件完成，
# 不能在文化的 ethnicities 加权列表里表达。
#
# 每个模型组仍只代表一个角色，但有 adult/child 两个 portrait type。
# 资格 trigger 不限年龄；type 按年龄选择缩小实体，避免未成年继续显示成人尺寸。
# 第 5 项是写在该 type 之前的分节注释（None = 不写）。挂在数据上而不是
# 写死在循环里，这样加角色时注释跟着走。
# 2026-09-24 用户授权接入终末地男模型：满足传统和美貌条件的任意性别角色，
# 可使用同一性别、任意年龄的模型类型；失格继承需纠正，大头画面的根因仍待实机定位。
# 分节注释挂在数据上（None = 不写）。四个先行角色保留当年的注记，批量角色不写。
_TYPE_NOTES = {
    "moe_lau": "# --- 洛茜（endfield_laurentina）---",
    "moe_per": ("# --- 洁尔佩塔（endfield_aglina）---\n"
                "# 594 骨。她的动画集是独立的一整套（骨数写在 .anim 文件头，\n"
                "# 骨数不匹配 = 引擎整段拒播 + entity 静默不渲染）。"),
    "moe_eld": ("# --- 艾尔黛拉（endfield_eldera）---\n"
                "# 2026-09-06 进表：表情按新管线重烘、动画集与物理全套重生成。"),
}
TYPES = {c["type"]: (c.get("sex", "female"),
                     f"minimum_age = {CR.PORTRAIT_ADULT_MINIMUM_AGE}",
                     c["head_entity"], c["torso_entity"],
                     _TYPE_NOTES.get(c["type"], f"# --- {c['name_cn']}（{c['slug']}）---"))
         for c in ACTIVE}

# Under-18 characters remain eligible. Their child portrait types must point to
# age-proportioned meshes; uniformly scaling adult geometry does not satisfy it.
CHILD_ENTITY_SUFFIX = "_child"
CHILD_PORTRAIT_SCALE = 0.75
# 每个 group 四个 sex×age type（本性别成人/儿童 + 异性成人/儿童，同一套实体）。
# 异性两个 type 服务于换装窗口的「忽略性别」开关：引擎按 sex+age 重挑 type，
# group 里没有该性别的 type 时异性角色匹配不到，模型不渲染。
PORTRAIT_TYPE_NAMES = tuple(
    name for c in ACTIVE for name, _sex, _age in X.portrait_types(c)
)


# 每个角色的建网格报告。`meshsettings` 必须按**该角色自己的** submesh 列表
# 生成 —— 缺一条整层就不渲染，而日志只报一行
# （见 [[ck3-every-submesh-needs-meshsettings]]：尾巴不渲染的真凶）。
REPORTS = {c["type"]: X.report_path(c["ck"]) for c in ACTIVE}


def _parts_of(tkey):
    """该角色 report 里的 parts。缺报告就返回空 —— 上层会报 meshsettings 缺失。"""
    f = REPORTS.get(tkey)
    if f is None or not f.is_file():
        return {}
    return (json.loads(f.read_text(encoding="utf-8")).get("parts") or {})


def _mesh_of(tkey, part):
    """type 键 + 部位 → (pdxmesh 名, .mesh 文件名)。

    从 TYPES 里的 entity 名反推前缀：`moe_lau_head_entity` → `moe_lau_`。
    这样加角色只改 TYPES 一处，不必再维护第二张名字表。
    """
    ent = TYPES[tkey][2 if part == "head" else 3]
    pfx = ent[:-len(f"{part}_entity")]
    return f"{pfx}{part}_mesh", f"{pfx}{part}.mesh"


# ---------------------------------------------------------------- 隐形贴图
#
# **必须在写 asset 之前生成。** `tex_ref()` 按「文件是否已存在」决定要不要
# 写 texture_* 那几行，所以干净构建时（mod/ 刚被清掉）这三个文件还不在，
# 8 个 `no_render` 层就一条贴图引用都拿不到 —— 那些层于是**照常渲染出来**。
#
# Eyes+ 就是其中一层：MMD 的眼睛高光穹顶，CK3 没有 additive 混合，任何
# shader 都会把它画成盖住虹膜的实心白。用户 2026-08-29 猜「洁尔佩塔眼睛
# 泛白是光源与材质反光系数共同导致反光过强」，方向是对的，而这就是那条
# 反光进入画面的入口。
#
# 原先它排在 DDS 转换之后（理由是「它本来就是 .dds，别被当成待转 PNG」），
# 但转换脚本只处理 PNG，所以那个顾虑不成立；而排在后面的代价是每次干净
# 构建都丢掉全部 no_render 层。
print("")
print("生成不可见层贴图:")
_inv = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "textures" / "make_invisible_texture.py")],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
# 编码在本地取 —— 脚本后面那个 `_enc` 定义在更下面，这块提前之后用不到它。
_inv_enc = getattr(sys.stdout, "encoding", None) or "utf-8"
for _line in (_inv.stdout or "").splitlines():
    if _line.strip():
        print("  " + _line.encode(_inv_enc, "replace").decode(_inv_enc,
                                                              "replace"))
if _inv.returncode != 0:
    print(f"  !! 生成失败 (exit {_inv.returncode})")
    if _inv.stderr:
        print(f"  {_inv.stderr[:800]}")

# **写 asset 必须排在 TYPES / REPORTS / _mesh_of 之后** —— asset_text() 现在按
# TYPES 循环出每个角色的 pdxmesh 与 entity，在 TYPES 定义之前调用会 NameError。
# **每个模型子 mod 出自己那份 `.asset`**，不是一份共用的。
# 共用那份会被路由整体归给某一个游戏包，于是只装另一个包时 mesh 全在、
# 没人描述、什么都不渲染且日志零报错（见 asset_text 的 docstring）。
#
# **文件名按游戏区分**（2026-08-29）：两包都叫 `moe_portrait.asset` 时 VFS
# 按路径覆盖，后加载的 endfield 把 gfl2 那份整份顶掉，芙洛伦的 entity 变成
# 未定义（详见 `moe_out.asset_name` 的注释）。
_asset_groups = {}
for _tk in TYPES:
    _ck = (MP.char_of(_mesh_of(_tk, "torso")[1][:-len("torso.mesh")]) or "flo")
    _asset_groups.setdefault(O.GAME_OF[_ck], set()).add(_ck)
for _g, _cks in sorted(_asset_groups.items()):
    _m = O.models(_g)
    w(O.asset_rel(_g), asset_text(_cks), mod=_m)
    print(f"    ({_m} 的 {O.asset_name(_g)} 含 {sorted(_cks)})")


# ---------------------------------------------------------------- 开场人物
# 每个二次元模型一个可玩的开场人物（用户要求「都放在西班牙那附近」）。
#
# 一个角色需要**六样东西**齐备，缺任何一样都会静默失效或报错：
#     dna_data 条目          指派 portrait type（引擎唯一接受的入口）
#     history/characters     角色本体
#     history/titles         持地 —— 未持地的角色不在地图上，游戏里找不到
#     bookmarks 条目         开局第一屏可选
#     bookmark_portraits     缺了报 "has no portrait in database"
#     两个 loc key + 贴图    缺了报 Missing loc / background not found
#
# 领地都在加泰罗尼亚，彼此相邻且**不在任何原版书签里**（原版 1066 组
# 伊比利亚只用了 d_toledo），所以不会和书签角色抢地。
#
# position 是书签地图的像素坐标。**写超范围不报错，卡片直接渲染到画面外**，
# 所以取值保守。参照原版 bm_1066_iberia：加利西亚 450,530、莱昂 620,180。
# 两张表（2026-09-06 拆开）：
#   ALL_CHARS       全部就绪角色 —— DNA / 书签肖像别名（换装窗口实时预览要它）/ 池注册
#   MOE_CHARACTERS  其中的**可玩开局角色**（登记表 playable 非空）—— 书签卡片 / history
#                   角色 / 王朝家族 / 纹章 / 伯爵领 / 书签 loc。批量导入的角色只进模型池
#                   与换装槽位（用户 2026-09-06 定），不在这张表里。
# 字段名与旧的 MOE_CHARACTERS 条目一致（type / name_en / name_cn / char_id + playable 各项），
# 下游 f-string 不用改。
ALL_CHARS = {c["ck"]: c for c in ACTIVE}
MOE_CHARACTERS = {c["ck"]: {**c, **c["playable"]} for c in ACTIVE if c["playable"]}
# 书签开局整体关闭（用户 2026-10-01）：持地要写 history/titles、书签要写 common/bookmarks，
# 都是改地图/开局数据，与其他 mod 冲突。关掉后不写 history 角色、王朝/家族、纹章、伯爵领、
# 书签、书签肖像、书签贴图与书签 loc。登记表的 playable 字段保留（王朝名进 name_list 仍用它）。
PLAYABLE_STARTS = False
_STARTS = MOE_CHARACTERS if PLAYABLE_STARTS else {}

MOE_START_DATE = "1066.9.15"


def portrait_type_text(only=None):
    """每个角色一个 group，每个 group 有一个 adult 与一个 child type。

    一个 group 对应一个角色；组内提供 adult 与 child 两个年龄槽位。
    引擎按 sex/age 选择槽位，资格判定与年龄分型保持独立。

    only：只产这一个 type 的 group（逐角色分文件用，归属见 moe_out.py）。
    """
    L = []
    for tname, (sex, age, head, torso, note) in TYPES.items():
        if only and tname != only:
            continue
        if note:
            for nl in note.split("\n"):
                L.append(nl)
        L.append(f"{GROUPS[tname]} = {{")
        L.append("\tcolors = {")
        L.append('\t\tskin = "gfx/portraits/skin_palette.dds"')
        L.append('\t\teye = "gfx/portraits/eye_palette.dds"')
        L.append('\t\thair = "gfx/portraits/hair_palette.dds"')
        L.append("\t}")
        L.append("\tproperties = {")
        L.append('\t\tskin = "gfx/portraits/skin_properties_palette.dds"')
        L.append('\t\teye = "gfx/portraits/eye_properties_palette.dds"')
        L.append('\t\thair = "gfx/portraits/hair_properties_palette.dds"')
        L.append("\t}")
        L.append("")
        # 四个 sex×age 槽位共用同一套 head/torso 实体。本性别成人排第一（DNA 的 type
        # 指向它）；异性两个 type 只为「忽略性别」手动换装，随机池仍按 is_<sex> 同性别分配。
        for n, (type_name, type_sex, slot) in enumerate(X.portrait_types(X.by_type(tname))):
            if n == 2:
                L.append("\t# 异性槽位：换装窗口「忽略性别」开关打开后手动换装的角色用它渲染。")
            L.append(f"\t{type_name} = {{")
            L.append(f"\t\tsex = {type_sex}")
            if slot == "adult":
                L.append(f"\t\t{age}")
                L.append(f'\t\thead = "{head}"')
                L.append(f'\t\ttorso = "{torso}"')
            else:
                L.append(f"\t\tmaximum_age = {CR.PORTRAIT_CHILD_MAXIMUM_AGE}")
                L.append(f'\t\thead = "{head}{CHILD_ENTITY_SUFFIX}"')
                L.append(f'\t\ttorso = "{torso}{CHILD_ENTITY_SUFFIX}"')
            L.append("\t}")
            L.append("")
        L.append("\tattach = {")
        L.append("\t\twhat = head")
        L.append("\t\twhere = torso")
        L.append("")
        L.append("\t\tjoint_attachment = {")
        L.append('\t\t\tparent_joint = "bn_m_root"')
        L.append('\t\t\tchild_joint = "bn_m_root"')
        L.append("\t\t}")
        L.append("\t}")
        L.append("}")
        L.append("")
    return "\n".join(L) + "\n"


def _dead_end_notes():
    """曾经实机否证过的走法，写进产物避免下一个人重试。"""
    return """
# ---------------------------------------------------------------------------
# 已实机否证的走法，别重试：
#
# **不能在 group 内补男性 type 指向原版实体**（2026-08-26）。试过 moe_male/
# moe_boy → head_basic_entity_male + male_body_entity，并把原版那三组
# joint_attachment 与 bn_m_root 并列。error.log 刷了 7442 条并持续增长：
#     male_body_entity has no attach point named bn_m_root
# 引擎**不会**静默丢弃 attach 里不存在的骨（那条规则只在 .anim 成立）。
# 不符合准入条件的男性走原版外观时仍留在 human group（靠文化 ethnicities
# 指向原版 ethnicity 达成）。符合条件的男模使用自有 custom entity/type，
# 不能在 Moe group 里塞入原版 male_body_entity。
#
# **attach 必须自己贴自己**（bn_m_root → bn_m_root），不能照抄原版的
# bn_sp_thoracic → head_root。两边坐标系约定不同：
#   原版 head.mesh 顶点在「头骨局部空间」，Y 25~55，靠 attach 到胸椎
#   （世界 106.5）才升到脖子上方的世界 131~162。
#   我们的 head.mesh 是从同一具全身骨架拆出来的，顶点已经是世界坐标
#   （Y 83~144）。再叠一次 spine_up(98.94) 会把头顶到 182~243，飞到
#   身体上方。同名骨互贴 = 零位移，head 与 torso 留在同一世界坐标系。
"""


PT_HEADER = f"""# 自定义二次元肖像 type group。原版 human group 零改动。
#
# type 名必须带 {GROUP}_ 前缀：portrait type 名是全局唯一的，
# 复用 male/female/boy/girl 会把它们从 human group 抢走。
#
# **一个角色一个 group**，各角色互不冲突。adult 与 girl/boy type 依据 sex + age
# 选择；资格没有年龄门槛。未成年角色使用同骨架、短身比例的 child head/torso 网格，
# 不能直接复用成人网格或只做整体等比缩小。DNA 的 type 只定位 group 与 gene 集。
#
# 角色落到哪个 group，由它的 ethnicity 决定（见 common/ethnicities/）。
"""
# **逐角色一个文件**（2026-08-28）。理由是模型要按来源
# 游戏拆成子 mod，而 portrait_types / customization_rules / genes / ethnicities
# / dna_data 五类都是逐角色的内容。一个文件装三个角色的话，拆分就得解析并重写
# 文件内容；按角色分文件后拆分只是按文件名路由，风险低得多。
for _t in TYPES:
    _ck = _t[len("moe_"):]
    w(f"common/portrait_types/10_{GROUP}_types_{_ck}.txt",
      PT_HEADER + portrait_type_text(only=_t)
      + (_dead_end_notes() if _t == "moe_flo" else ""), mod=O.models_of(_ck))

# ---------------------------------------------------------------- group 注册
# 字段陷阱：
#   sex_only_genes 的键是性别 enum（male/female），不是 portrait type 名
#   accessories / hidden_slider_genes 的值是 gene 名，原版那些 gene 绑在 human 上，
#   对新 group 不适用，所以这里不写（要 Ruler Designer 支持时再补自己的 gene）
CUSTOMIZATION = f"""# {GROUP} group 的注册入口。规则名必须等于 group 名，
# **每个 group 都要一条**（我们有三个 group，一个角色一个）。
#
# 引擎双向校验：
#   "Missing portrait customization rule entry for portrait group {{}}"
#   "Customization rule {{}} does not exist as a portrait group"
#
# 刻意保持最小集：不引用任何原版 gene（那些 gene 绑在 human group 上）。
# 手持道具的自造 special gene 使用独立 gene group；该 group 必须在这里注册，
# 否则 gene 文件语法完全正确也会被静默丢弃，直到 modifier 加载时才报
# `could not find gene`。
"""


def _cust_block(g):
    return f"""{g} = {{
\tdefault_age = 25
\tmin_age_for_sexuality = 10

\tgene_groups = {{
\t\t{g}_portrait_group = {g}_portrait_group
\t\tanatomy = {{
\t\t\tbody = full_body
\t\t\thead_neck = face
\t\t\tears = face
\t\t\tface = face
\t\t\teyes = face
\t\t\tnose = face
\t\t\tmouth = face
\t\t}}
\t}}
\t# 1.20 起每个 group 都必须声明，否则 'X is missing required custom_* for accessory
\t# color override shader support'（2026-10-01 实测）。照抄原版 human。
\taccessory_color_override_block_0 = custom_clothes
\taccessory_color_override_block_1 = custom_headgear
\taccessory_color_override_block_2 = custom_cloaks
\taccessory_color_override_block_3 = custom_legwear
}}
"""


for _t, _g in GROUPS.items():
    _ck = _t[len("moe_"):]
    w(f"gfx/portraits/customization_rules/10_{GROUP}_customization_{_ck}.txt",
      CUSTOMIZATION + _cust_block(_g), mod=O.models_of(_ck))

# ---------------------------------------------------------------- 独立 gene 集
# 关键语法：容器内首行 portrait_group = <group>。
# gene 名可与原版同名 —— group 间命名空间隔离，实测不报 already exist。
GENES_COLOR = f"""# {GROUP} 各 group 专属 color gene。
#
# 关键语法：容器内首行 portrait_group = <group>。
# 实测只有这个位置有效：文件顶层写、gene 内写都被静默忽略（仍按 human 解析），
# 用 group 名作容器键、容器名加前缀都报 Unexpected token。
#
# **每个 group 一个独立容器** —— gene 不能跨 group 共享。三份内容相同，
# 只有 portrait_group 一行不同。gene 名可与原版同名（命名空间隔离）。
"""


def _color_block(g):
    return f"""color_genes = {{
\tportrait_group = {g}

\thair_color = {{
\t\tgroup = hair
\t\tcolor = hair
\t\tblend_range = {{ 0.0 0.0 }}
\t}}
\tskin_color = {{
\t\tsync_inheritance_with = hair_color
\t\tgroup = body
\t\tcolor = skin
\t\tblend_range = {{ 0.55 0.65 }}
\t}}
\teye_color = {{
\t\tsync_inheritance_with = hair_color
\t\tgroup = eyes
\t\tcolor = eye
\t\tblend_range = {{ 0.0 0.0 }}
\t}}
}}
"""

GENES_MORPH = f"""# {GROUP} group 专属 morph gene。
#
# 当前刻意留空：morph gene 的 `setting = {{ attribute = "X" ... }}` 里的 X
# 必须是头模 .asset 里 `attribute = {{ name = "X" additive_animation = ... }}`
# 声明过的名字，而每个 additive_animation 又要一个真实的 .anim 文件。
# 原版 female_head 有约 40 个这样的 attribute + 40 个 .anim。
#
# 曾经照抄原版写了 gene_chin_forward，结果每个 type 报两条：
#   The following errors occurred when building attribute list for
#   gene_chin_forward:chin_forward_neg:moe_male
#      could not find attribute "chin_forward"
# 因为我们的 moe_portrait.asset 没有 chin_forward 这个 attribute。
#
# 结论：捏脸滑条要等自己烘出 additive_animation 的 .anim 之后再加，
# 现在留空容器（引擎接受空的 morph_genes 容器）。
#
# 后续「预设脸 + 局部变化」的落地路径：
#   1. 在 Blender 里对头模做形变（下巴前移、眼距等），导出为 .anim
#   2. 在 moe_portrait.asset 的 pdxmesh 块里声明
#      additive_animation = {{ id = "chin_forward" type = "moe_chin_forward.anim" }}
#      和 attribute = {{ name = "chin_forward" additive_animation = "chin_forward" }}
#   3. 回到这里加 gene 块，type 块用本 group 的 type 名
#      （{", ".join(TYPES)}），写 male/female 会报
#      "Type male is not part of the <group> group"
#
# **每个 group 一个容器**，同 color gene。
"""

# 逐角色写出（w(..., mod=O.models_of(_ck)) 直写对应子 mod）。
for _t, _g in GROUPS.items():
    _ck = _t[len("moe_"):]
    # 1.20 起 gene 名全局（2026-10-01 实测）：自造同名 hair/skin/eye_color 顶掉原版，
    # 原版 DNA 全报 missing gene、开局崩溃；改名则原版 DNA 报缺我们的 gene。
    # 所以不写自己的 color gene；旧文件在 trash/20261001_color_genes。
    w(f"common/genes/41_{GROUP}_genes_morph_{_ck}.txt",
      GENES_MORPH + f"morph_genes = {{\n\tportrait_group = {_g}\n}}\n", mod=O.models_of(_ck))

# ---------------------------------------------------------------- 衣服 accessory
# 只有 moe_clothes.mesh 真的导出来了才写这几个文件，否则引擎会因为
# accessory 指向不存在的 entity 而报错。
CLOTHES_MESH_OK = (MP.models_dir("flo") / "moe_clothes.mesh").is_file()

if CLOTHES_MESH_OK:
    # 1) accessory 定义：把 entity 挂到躯干的姿势上
    #
    # shared_pose_entity = torso 是整套机制的关键 —— 它让衣服**不需要**自己
    # 的 state/animation，直接用躯干那 189 骨的当前姿势做蒙皮。
    # 原版 gfx/portraits/accessories/clothes.txt 每一条都是这个写法。
    #
    # required_tags = "" 表示无条件可用（原版用 tags 做「胖/瘦/孕」筛选，
    # 我们只有一套几何，不筛）。
    ACCESSORY = f"""# {GROUP} 的衣服 accessory。
#
# 机制（照原版 gfx/portraits/accessories/clothes.txt）:
#   shared_pose_entity = torso  → 衣服共用躯干 entity 的骨架与当前姿势，
#   所以衣服 .asset 里没有 state、没有 skeletal_animation_set。
#   这也是衣服能跟着 17 个身体动作一起动的原因：它根本没有自己的动画，
#   完全跟随躯干。
#
# 注意衣服 mesh 的骨序必须与躯干**完全一致**（我们是同一套 189 骨，
# 由 tools/model/bake_moe_expressions.py 一次导出三件保证一致）。
#
# **portrait_group = {GROUP} 是必须的**（accessory 定义内、entity 之前）。
# 缺了引擎报：
#     [portraitaccessories.cpp:163] accessory group [clothes:{GROUP}_clothes]
#     has accessory {GROUP}_clothes_default which is only scripted to work
#     in portrait group human not {GROUP}
# 后果不是告警而是**衣服整件不渲染** —— accessory 的 group 归属由 accessory
# **自身定义**决定（引擎里 SAccessoryGroup 内嵌在 CAccessoryGeneCategory），
# 在 accessory_genes 容器里声明 portrait_group 只解决 gene 侧，不解决这里。
# 原版全库没有这个字段是因为 human 是引擎硬编码默认值。
{GROUP}_clothes_default = {{
\tportrait_group = {GROUP}
\tentity = {{ required_tags = "" \t\tshared_pose_entity = torso \t\tentity = "{ENTITY_CLOTHES}" }}
}}
"""
    w(f"gfx/portraits/accessories/{GROUP}_clothes/{GROUP}_clothes.txt", ACCESSORY)

    # 2) accessory_gene：让引擎实际选中上面那条 accessory
    #
    # 结构是原版的四层（special_genes > accessory_genes > 槽名 > 模板 > 性别）。
    # 槽名用 clothes —— 与原版同名不冲突，因为 portrait_group 隔离命名空间
    # （与 color/morph gene 同一个已验证结论）。
    # 四个 type 都要给：moe_male/female/boy/girl，缺一个那个 type 就裸体。
    types = [f"{GROUP}_male", f"{GROUP}_female", f"{GROUP}_boy", f"{GROUP}_girl"]
    lines = [f"# {GROUP} group 的衣服 accessory gene。",
             "#",
             f"# portrait_group = {GROUP} 必须是 accessory_genes 容器内首行 ——",
             "# 与 color/morph gene 同一条规则（写在文件顶层会被静默忽略）。",
             "#",
             "# index 从 0 开始，每个槽独立编号。",
             "special_genes = {",
             "\taccessory_genes = {",
             f"\t\tportrait_group = {GROUP}",
             "",
             "\t\tclothes = {",
             f"\t\t\t{GROUP}_clothes = {{",
             "\t\t\t\tindex = 0"]
    for t in types:
        lines.append(f"\t\t\t\t{t} = {{")
        lines.append(f"\t\t\t\t\t1 = {GROUP}_clothes_default")
        lines.append("\t\t\t\t}")
    lines += ["\t\t\t}", "\t\t}", "\t}", "}", ""]
    w(f"common/genes/42_{GROUP}_genes_accessory.txt", "\n".join(lines))

    # 3) portrait_modifier：**让衣服真正穿上身的那一层**
    #
    # 前两层（accessory 定义 + gene）只表达「有这件衣服、哪个 type 能选它」，
    # 真正把它挂到角色上靠 dna_modifiers.accessory。缺这一层时衣服
    # **静默不渲染且日志零报错** —— 三次改 accessory/DNA 让日志错误归零，
    # 衣服一次都没出现，就是因为断点在这里。
    #
    # 判定实验：把 moe_female 的 torso 临时指向 ENTITY_CLOTHES 时衣服完整
    # 渲染，证明 mesh/贴图/骨骼/entity 全健全（存证 work/shot/clothes_diag/）。
    #
    # 结构照原版 portrait_modifiers/01_clothes_base.txt 的 clothes_base 容器。
    # 原版没有 portrait_group 字段（human 是引擎硬编码默认），自定义 group
    # 必须显式声明。
    MODIFIER = f"""# {GROUP} group 的衣服挂载。**这一层是让衣服真正穿上身的关键。**
#
# 三层机制缺一不可，前两层齐全但缺这一层时衣服静默不渲染、日志零报错：
#   1. accessory 定义  gfx/portraits/accessories/{GROUP}_clothes/
#   2. accessory gene  common/genes/42_{GROUP}_genes_accessory.txt
#   3. portrait_modifier（本文件）—— 靠 dna_modifiers.accessory 实际挂上
{GROUP}_clothes_base = {{
\tportrait_group = {GROUP}

\tusage = game
\tselection_behavior = max
\tfallback = {GROUP}_clothes_default_outfit
\tpriority = 1

\t# 只有一套衣服，给固定正权重让它必中。
\t{GROUP}_clothes_default_outfit = {{
\t\tdna_modifiers = {{
\t\t\taccessory = {{
\t\t\t\tmode = add
\t\t\t\tgene = clothes
\t\t\t\ttemplate = {GROUP}_clothes
\t\t\t\trange = {{ 0 1 }}
\t\t\t}}
\t\t}}
\t\tweight = {{
\t\t\tbase = 1000
\t\t}}
\t}}
}}
"""
    w(f"gfx/portraits/portrait_modifiers/10_{GROUP}_clothes.txt", MODIFIER)
    print(f"  衣服 accessory + gene + portrait_modifier 已写（entity {ENTITY_CLOTHES}）")
else:
    print("  跳过衣服 accessory：moe_clothes.mesh 不存在")

# ---------------------------------------------------------------- DNA（角色指派入口）
# accessory gene 在 DNA 里的值是 { "模板名" 索引 "模板名" 索引 }。
# **必须照原版格式，两处细节都不能省**（`common/dna_data/00_dna.txt`）:
#
#     clothes={ "fp1_commoner_clothes" 177 "most_clothes" 0 }
#     headgear={ "fp1_common" 140 "no_headgear" 0 }
#
# 1. 模板名**要加引号**。
# 2. 两组是显性/隐性等位，**隐性那组的索引原版一律给 0**。
#
# 曾经写成 `clothes = { moe_clothes 255 moe_clothes 255 }`（无引号、
# 隐性也给 255），引擎不报任何错，但**衣服整件不渲染** —— 这类
# 「语法能过、语义无效」的写法只能靠对照原版发现。
CLOTHES_GENE_LINE = (f'\t\t\tclothes = {{ "{GROUP}_clothes" 255 "{GROUP}_clothes" 0 }}\n'
                     if CLOTHES_MESH_OK else "")
# DNA_NAME 保留给别处引用（history 角色的 dna = 字段用主角色那个）。
DNA_NAME = X.BY_CK["flo"]["type"]

# 每个角色一个 DNA 条目。**必须有条目，否则该 type 没有任何角色使用**，
# 而且 hair gene 缺失时 `portrait_hair` shader 那块不渲染 —— 洛茜的
# **尾巴归在 hair 材质里**（moe_lau_torso.mesh 块 13，T_actor_wulfa_hair_02_D，
# 10414 条尾巴权重条目），所以表现为「游戏里尾巴没有渲染」（2026-08-22）。
_dna_blocks = {}
for _ck, _cs in ALL_CHARS.items():
    _hdr = ""
    if _ck == "lau":
        _hdr = (f"\n# 洛茜（endfield_laurentina）。**必须有 DNA 条目，否则 "
                f"{_cs['type']} type\n"
                "# 没有任何角色使用**，而且 hair gene 缺失时 `portrait_hair` "
                "shader 的那块\n"
                "# 不渲染 —— 洛茜的**尾巴归在 hair 材质里**"
                "（moe_lau_torso.mesh 块 13，\n"
                "# T_actor_wulfa_hair_02_D，10414 条尾巴权重条目），"
                "所以表现为\n"
                "# 「游戏里尾巴没有渲染」（2026-08-22 用户发现）。\n")
    _dna_blocks[_ck] = f"""{_hdr}{_cs['type']} = {{
\tportrait_info = {{
\t\ttype = {_cs['type']}
\t\tgenes = {{
\t\t\thair_color = {{ 137 167 111 140 }}
\t\t\tskin_color = {{ 46 75 44 75 }}
\t\t\teye_color = {{ 195 192 123 200 }}
{CLOTHES_GENE_LINE}\t\t}}
\t}}
\tenabled = yes
}}
"""

DNA = f"""# {GROUP} group 的角色 DNA。
#
# portrait_info 内的 type 字段是唯一被引擎接受的 group 指派入口。
# 实测 portrait_type / portrait_group / portrait_customization 在 dna_data 里
# 全报 Unexpected token。
#
# 玩家可控途径：把这个 dna 挂到角色上即可 —— history 文件的 dna = 字段，
# 或游戏内用 effect 赋予（特质/宗教/决定/角色互动都能作为触发条件）。
"""
# 逐角色写出（w(..., mod=O.models_of(_ck)) 直写对应子 mod）。
for _ck, _blk in _dna_blocks.items():
    w(f"common/dna_data/40_{GROUP}_dna_{_ck}.txt", DNA + _blk, mod=O.models_of(_ck))

# ---------------------------------------------------------------- animations 覆盖
# animations.txt 里多数动画用 `default = ` 通配，对任何 type 生效；
# 少数是逐 type 声明的（male = {...} female = male girl = male boy = male），
# 新 type 不在其中就报
#   "Animation 'X' does not have any default animations scripted for 'moe_male'"
#
# 这个目录不走同名键覆盖，是校验型的 —— 在新文件里补声明无效
# （10_ 与 zz_ 前缀都试过，报错仍指向原版 animations.txt 的行号）。
# 只能同名整体覆盖。所以从游戏目录实时读原版再改，不把改好的文件当源码维护，
# CK3 更新后重新生成即可。
import re as _re

# ---------------------------------------------------------------- 待机姿势表
# 用户 2026-08-28 第 3 项要的「换姿势」。**必须定义在下面的 animations.txt
# 补丁之前** —— 那段要读这两个名字，Python 是顺序执行的。
POSE_FLAG_PREFIX = f"{GROUP}_pose_"

# 第二列是 **animations.txt 里引用的名字 = entity 的 state 名**，既不是 .anim
# 文件名、也不是 skeletal_animation_set 里的 animation id（三套命名空间，见
# [[ck3-portrait-animation-four-name-layers]]）。
#
# 我第一版按 animation id 猜（`idle_entry` / `emotion_angry_start` 之类），
# 10 个全错 —— 真实 id 长这样 `moe_body_emotion_angry`（带部位前缀且逐角色）。
# 引擎在 animations.txt 里查的是 **state 名**，我们注册了 758 个。
#
# **每一项都实测过头身双边都有 .anim**：单边的会让头脱开躯干
# （[[ck3-head-torso-need-paired-anims]]）。因此筛掉了 `idle_calm`（无 state）、
# `page_flipping_loop`（缺身）、`throneRoom_conversation`（无 state）。
# `_check_poses()` 在构建末尾再核对一次。
#
# 归**核心**：姿势走 animations.txt 的 idle 块，那份文件整份只能有一个、归
# 核心。模型子 mod 想加姿势只能经由核心这张表 —— 是引擎的单文件覆盖语义决定
# 的，不是设计选择。
# 第 5 列是**道具的 modifier pack**，没有就是 None。
#
# **不能省**：道具不是跟着动画走的，是跟着 `portrait_modifier_pack` 走的。
# 原版 `page_flipping` 块里写着 `portrait_modifier_pack = paged_book`，那才是
# 书的来源。我第一版只写 `animation = {...}`，结果选了「翻书」姿势动作对了但
# 手里空着 —— 与用户最初报的「只有动作没有书」一模一样，只是原因不同
# （那次是基因缺条目，这次是漏了 pack）。
#
# 取值是实测的：把这 9 个 state 名在原版 animations.txt 里逐个反查同一子块内的
# `portrait_modifier_pack` / `animations_props`，只有翻书和持弓有。
# 2026-09-06 起姿势表**不再手写**：tools/lib/moe_pose_catalog.py 读原版 animations.txt 的全部
# 顶层块（252 个可用），每块取默认 state + default 子块直接层的道具行。五列形状保留给消费方：
#   (键=块名, state, 中文名, 英文名, 道具行列表)
# 第 5 列不再是单个 pack 名，而是 [("pack", 名) | ("modifier", [(容器, 值)...])]，
# 用 _PC.props_text 排成多行 —— 必须多行：下面第四处改动追加 `moe_<ck>_` 版本的正则要求
# `portrait_modifier = {` 后换行、同缩进闭合。两个关键字查不同的库（pack 在 *.modifierpack，
# animations_props 在 portrait_modifiers/），照抄原文就不会混用。
import moe_pose_catalog as _PC
_POSE_CAT = _PC.load()
POSES = [(q["key"], q["state"], q["cn"], q["en"], q["props"]) for q in _POSE_CAT]
POSE_GROUPS = _PC.groups(_POSE_CAT)      # [(中文组名, 英文组名, [pose dict])]
if _POSE_CAT.skipped:
    print(f"  姿势目录：{len(POSES)} 个，跳过 {len(_POSE_CAT.skipped)} 个块 "
          f"{[k for k, _r in _POSE_CAT.skipped]}")

GAME_ANIM = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III"
                 r"\game\gfx\portraits\portrait_animations\animations.txt")
if GAME_ANIM.is_file():
    _anim = GAME_ANIM.read_text(encoding="utf-8-sig")
    _pat = _re.compile(r"^(\t)(boy) = (male|female)\s*$", _re.M)

    def _add_moe(m):
        ind, who, tgt = m.groups()
        extra = "".join(f"\n{ind}{t} = {tgt}" for t in PORTRAIT_TYPE_NAMES)
        return f"{ind}{who} = {tgt}" + extra

    _new, _n = _pat.subn(_add_moe, _anim)

    # ---- 第二处改动：强制 head 与 torso 引用**同一个 state 名**
    #
    # 原版有 174 处写成不同名，例如
    #     head = "misc_pregnant_entry"  torso = "misc_pregnantFat_entry"
    # 原版这样没问题：它的 `male_head.mesh` 只有 61 骨、**不含脊椎**，头部动画
    # 不携带躯干姿态，所以躯干播哪个体型变体都不影响头。
    #
    # 我们的 head.mesh 带完整脊椎链，头部动画的脊椎通道是从**同名**身体动画
    # 嫁接来的（见 README「头身配对必须同名」）。head/torso 指到不同 state
    # 时两侧脊椎不一致 → **动画过程中头和身子分离**。用户 2026-08-28 报的
    # anger 分离就是这一层，我先只修了 `.asset` 内同名 state 的配对，
    # 漏了这一层，所以修完问题依旧。
    #
    # 我们一个角色只有一具 mesh，Fat/Dwarf 变体用不上，所以统一取 **head 那一
    # 侧的 state 名**（头部动画带表情 morph 通道，以它为准更安全）。
    # **不能无条件统一**：`animations.txt` 是全局共享的，原版体型也读它。
    # 我第一版直接把 torso 改成 head 名，把
    #     head = "throneRoom_conversationDwarf1_entry"
    #     torso = "throneRoom_conversationFatDwarf1_entry"   ← 原版合法
    # 改成两侧都是 Dwarf1 —— 而 `...Dwarf1_entry` 在**任何** body `.asset`
    # 里都没声明，于是 28 条
    #     Unknown animation state '...' (part 'torso', type 'male'/'moe_flo'/...)
    # 每个 portrait type 各报一遍。所以只在 head 名**确实是躯干合法 state**
    # 时才统一；不合法的保留原样（那种配对原版体型才走得到，我们的角色
    # 走不到，因为权重条件是 fat/dwarf）。
    _torso_states = set()
    for _bd in ("male_body", "female_body"):
        _p = GAME / "gfx" / "models" / "portraits" / _bd
        if not _p.is_dir():
            continue
        for _f in _p.glob("*.asset"):
            _torso_states |= set(_re.findall(
                r'state\s*=\s*\{\s*name\s*=\s*"([^"]+)"',
                _f.read_text(encoding="utf-8-sig", errors="replace")))

    _PAIR = _re.compile(r'head = "([^"]+)"(\s+)torso = "([^"]+)"')
    _nfix = [0]
    _skip = []

    def _same_state(m):
        hd, gap, ts = m.groups()
        if hd == ts:
            return m.group(0)
        if _torso_states and hd not in _torso_states:
            _skip.append((hd, ts))
            return m.group(0)
        _nfix[0] += 1
        return f'head = "{hd}"{gap}torso = "{hd}"'

    _new = _PAIR.sub(_same_state, _new)
    _left = sum(1 for a, _g, b in _PAIR.findall(_new) if a != b)
    print(f"  animations.txt：head/torso 统一到同名 state，改写 {_nfix[0]} 处"
          f"（躯干合法 state {len(_torso_states)} 个；"
          f"head 名躯干不认、保留原样 {len(_skip)} 处；剩余异名 {_left} 处）")
    if _skip:
        print(f"     保留的前 3 处：{_skip[:3]}")

    # ---- 第三处改动：把玩家选的待机姿势接进 idle 块（第 3 项）
    #
    # idle 的结构是 `idle = { default = { default = {...} <变体> ... } }`，
    # 每个变体带 `weight = { base = 0 modifier = { add = N <trigger> } }`，
    # 引擎按权重挑。所以加一个 base 0、命中时 +10000 的变体就能压过原版全部
    # 变体（原版最高也是 10000，但那些要 incapable / imprisoned 之类才触发，
    # 与「玩家显式选了姿势」不会同时成立）。
    #
    # **插在 `default = {` 那一层里面**，不是 idle 的顶层 —— 顶层的键是
    # portrait type 名，插那儿会被当成一个不存在的 type。
    _idle = _re.search(r"^idle = \{\n\tdefault = \{\n", _new, _re.M)
    if _idle:
        _blk = "".join(
            f"\n\t\t{GROUP}_pose_{key} = {{\n"
            f'\t\t\tanimation = {{ head = "{state}" torso = "{state}" }}\n'
            # 道具靠 pack 挂，不靠动画。漏了就是「动作对了手里空着」。
            #
            # **两个关键字查的是不同的库**，名字像但不能混用：
            #   portrait_modifier_pack = X   →  X 必须在 *.modifierpack 里
            #                                   （paged_book / one_handed_sword）
            #   portrait_modifier = { animations_props = X }
            #                                →  X 在 portrait_modifiers/*.txt 里
            #                                   （random_idle_bow）
            # 混用的报错是 `Failed to find portrait modifier pack 'X'`，
            # 而那个 pack 明明在原版里存在 —— 我据此一度以为是被自己的文件
            # 覆盖了，查错了方向。
            + _PC.props_text(pack, "\t\t\t")
            + f"\t\t\tweight = {{\n"
            f"\t\t\t\tbase = 0\n"
            f"\t\t\t\tmodifier = {{\n"
            f"\t\t\t\t\tadd = 10000\n"
            # Portrait animation weights run in a restricted character scope.
            # CK3's own animations use has_character_flag here; has_variable is
            # rejected at runtime even though the surrounding scope is a character.
            f"\t\t\t\t\thas_character_flag = {POSE_FLAG_PREFIX}{key}\n"
            f"\t\t\t\t}}\n"
            f"\t\t\t}}\n"
            f"\t\t}}\n"
            # idle 是默认姿势，不需要变体（没选=原版行为），所以跳过
            for key, state, _cn, _en, pack in POSES if key != "idle")
        _new = _new[:_idle.end()] + _blk + _new[_idle.end():]
        print(f"  animations.txt：接了 {len(POSES) - 1} 个可选待机姿势")
    else:
        print("  !! animations.txt 里找不到 `idle = {` + `default = {` 两行，"
              "姿势切换没接上（表现是选了姿势没反应、零报错）")

    _synthetic_poses = [pose for pose in _POSE_CAT if pose["synthetic"]]
    _new += "\n" + "\n".join(_PC.synthetic_block_text(pose) for pose in _synthetic_poses)
    _preview_blocks = set(_re.findall(r"^(\w+)\s*=\s*\{", _new, _re.M))   # 原版有 `japanese_war_fan= {`
    _missing_previews = [pose["key"] for pose in _POSE_CAT if pose["key"] not in _preview_blocks]
    if _missing_previews:
        raise SystemExit(f"Missing portrait preview blocks: {_missing_previews}")
    print(f"  animations.txt: {len(_synthetic_poses)} synthetic preview blocks registered")

    # ---- 第四处改动：给道具引用追加我们自己的版本（四点计划第 2 项）
    #
    # 上面已经是原版全文 + 别名/姿势扩展，`animations_props = X` /
    # `portrait_modifier_pack = X` 原样保留 —— 但 X 是原版全局容器/pack 名，
    # 它的模板里没有我们 group 的 type 条目，引擎查不到就静默跳过
    # （[[ck3-gene-and-modifier-names-are-global]]，实测证据是「翻书没有
    # 书」而不是报错）。tools/build/build_moe_props.py 已经按同名 item/pack 生成了
    # 我们自己的版本（`moe_<ck>_<原名>`），这里在每处引用旁**追加**一条，
    # 不改原有那条 —— 原版角色照旧走原版，我们的角色额外拿到自己那份。
    _pc_path = ROOT / "cache" / "props_chain.json"
    # **缺了就当场生成，不能静默跳过**（2026-09-05）：这份清单住在不入库的
    # cache/，被当天的清理扫进 trash 后这里直接走 else 分支，animations.txt 少了
    # 1662 行道具注入（翻书/持弓全坏），构建照报成功。
    if not _pc_path.is_file():
        import subprocess as _sp
        _pc_path.parent.mkdir(parents=True, exist_ok=True)
        _r = _sp.run([sys.executable, str(ROOT / "tools" / "build" / "survey_props_chain.py")],
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
        if _r.returncode != 0 or not _r.stdout.strip():
            raise SystemExit("**survey_props_chain.py 失败**，拒绝在没有道具链的情况下写 "
                             "animations.txt：" + _r.stderr[-800:])
        _pc_path.write_text(_r.stdout, encoding="utf-8")
        print(f"  已重新生成 {_pc_path.relative_to(ROOT)}")
    if _pc_path.is_file():
        _pc = json.loads(_pc_path.read_text(encoding="utf-8"))
        # `items`/`packs` 是 `{名: 详情}` 的 dict（见 tools/build/survey_props_chain.py 的
        # json.dump），**不是 list-of-tuple** —— 曾写成 `{it[0] for it in
        # _pc["items"]}` 当成列表遍历，直接 iterate dict 拿到的是键的字符串本身，
        # `it[0]` 变成「键字符串的第 1 个字符」，于是 `_our_items` 里全是单字母，
        # 匹配永远不中，注入计数静默报 0（不报错，animations.txt 原样直通）。
        _our_items = set(_pc["items"])
        _our_ck = [t.split("_", 1)[1] for t in TYPES]      # flo / lau / per / ...

        # `portrait_modifier_pack = X` **不再追加我们的行**（2026-09-06 晚）：一个变体只取一行 pack，
        # 追加 24 行静默丢失（翻书/读书/写字实机空手）。X 的定义由 build_moe_props.py 以核心覆盖
        # 整份 animations.modifierpack 的方式并入我们的键，原版这一行原样保留即可。

        # `animations_props = X` 嵌在 `portrait_modifier = { ... }` 里（原版
        # 这类块内部不再嵌套花括号，抽样 135 处验证过），用同缩进反向引用
        # 配平首尾花括号，避免正则跨块越界。
        _item_hits = [0]

        def _inject_item(m):
            ind, body = m.group("ind"), m.group("body")
            am = _re.search(r'animations_props\s*=\s*(\w+)', body)
            if not am or am.group(1) not in _our_items:
                return m.group(0)
            _item_hits[0] += 1
            name = am.group(1)
            # portrait_modifier 内是「容器名 = item 名」，不是固定字段
            # `animations_props`。我们的容器和 item 都有角色前缀，必须一起改。
            # **合并进原版同一块**，不追加独立块：原版 186 个变体每个只有一块 portrait_modifier，
            # 多个道具是块内多个键。旁边追加 24 个独立块时实机走这条路径的姿势全部没道具
            # （2026-09-06 宰相/弩/骑马/乐器）；pack 路径同一种丢法，由核心覆盖 modifierpack 并入键。
            extra = "".join(f"{ind}\tmoe_{ck}_props = moe_{ck}_{name}\n" for ck in _our_ck)
            return f"{ind}portrait_modifier = {{\n{body}{extra}{ind}}}"

        _new = _re.sub(
            r'(?P<ind>[ \t]*)portrait_modifier = \{\n'
            r'(?P<body>(?:[ \t]+[^\n]*\n)*?)'
            r'(?P=ind)\}',
            _inject_item, _new)

        print(f"  animations.txt：portrait_modifier 块并入我们的键 —— item {_item_hits[0]} 处"
              f"（每处 × {len(_our_ck)} 个 group）；pack 行不追加，定义在核心覆盖的 modifierpack 里")
    else:
        print(f"  !! 找不到 {_pc_path}，跳过道具引用注入"
              f"（先跑 python tools/build/build_moe_props.py 生成它）")

    w("gfx/portraits/portrait_animations/animations.txt", _new)
    print(f"  animations.txt 覆盖版：补了 {_n} 处逐 type 声明")
else:
    print(f"  !! 找不到原版 animations.txt: {GAME_ANIM}")

# ================================================================ 种族（ethnicity）
# 关键坑：文化的 ethnicities 列表若指向原版 ethnicity（如 caucasian_blond），
# 该 ethnicity 隶属 human portrait group。角色的 DNA 是 moe group，
# ethnicity 是 human group —— 两者 group 不一致时，引擎判定「ethnicity 与当前
# portrait group 不同」，会静默重随机 DNA（回落到 ethnicity 所在 group，
# 即 human）。这才是书签预览正常、实际 gameplay 里立绘变回原版历史角色的根因。
#
# 证据链：
#   1. ck3.exe 内字符串 "Set the ethnicity for this character, if it is of
#      a different portrait group and they have DNA it will be randomized"
#   2. work/probe_group/common/ethnicities/02_probe_ethnicity.txt 曾经验证过
#      common/ethnicities/*.txt 顶层接受 portrait_group = <group> 字段，
#      隔离环境实测 0 错误（该实验做完后未回填进正式 mod，是本 bug 的直接成因）。
#
# 修复：新增一个属于 moe group 的 ethnicity，template 复用 caucasian_blond
# 的配色（继承 skin_color/eye_color/hair_color 权重表），文化改指向它。
#
# ---------------------------------------------------------------------------
# **一个 group 一个 ethnicity**，而且颜色必须锁死。理由：
#
# ethnicity 是「角色落到哪个 group」的唯一决定者。`set_ethnicity` 跨 group
# 时引擎会重随机 DNA（exe 那句话的后半段），而**没有 set_dna 这种 effect**
# （扫遍 exe 只有 RandomizeEthnicityDnaFromCulture/FromCurrent 两个随机化
# 入口）。所以不能靠「先切 group 再补 DNA」保住颜色。
#
# 解法是让「随机」本身没有自由度：ethnicity 的 color gene 格式是
#     权重 = { x_min y_min x_max y_max }        （0.0~1.0 的 palette UV）
# 写成零宽范围（min == max）后随机结果恒等于那个点，DNA 就是确定的。
#
# 数值从 dna_data 的 0~255 整数换算：137/255=0.537、167/255=0.655 等。
# 三个 ethnicity 的数值当前完全相同 —— 贴图本来就烘死了颜色，palette
# 只影响 iris 染色一处（见 README「虹膜 alpha 必须为 0」一节）。
#
# **不写 template**：原版 template 会带进大量 morph gene（gene_eyebrows_shape
# 之类），那些 gene 不在我们 group 的 gene 集里，会报 "Failed to find gene
# X in portrait type Y"。我们 group 只有 3 个 color gene，逐个给全即可。
_DNA_COLORS = {"hair_color": (137, 167), "skin_color": (46, 75),
               "eye_color": (195, 192)}

# 该文化默认用的原版 ethnicity（human group）。取自原版拜占庭文化
# （common/culture/cultures/00_byzantine.txt: 100 = mediterranean_byzantine）。
VANILLA_ETHNICITY = "mediterranean_byzantine"


def _locked(name):
    x, y = _DNA_COLORS[name]
    fx, fy = x / 255.0, y / 255.0
    return (f"\t{name} = {{\n"
            f"\t\t100 = {{ {fx:.4f} {fy:.4f} {fx:.4f} {fy:.4f} }}\n"
            f"\t}}\n")


ETHNICITIES = {t: f"{t}_ethnicity" for t in TYPES}
ETH_HEADER = f"""# {GROUP} 各 group 的 ethnicity。**这是 group 指派的唯一入口。**
#
# 颜色写成零宽范围（min == max）以锁死「重随机」的结果 —— 详见
# tools/build/build_moe_mod.py 里这一段的注释。
"""
# 逐角色写出（w(..., mod=O.models_of(_ck)) 直写对应子 mod）。
for _t, _eth in ETHNICITIES.items():
    _ck = _t[len("moe_"):]
    w(f"common/ethnicities/40_{GROUP}_ethnicity_{_ck}.txt",
      ETH_HEADER + f"\n{_eth} = {{\n"
      f"\tportrait_group = {GROUPS[_t]}\n"
      f"\tvisible = no\n"
      + "".join(_locked(c) for c in _DNA_COLORS) + "}\n", mod=O.models_of(_ck))

# ================================================================ 文化
# 用户 2026-09-05 定：moe 是**独立的一支 heritage（大项）**，以东罗马文化为
# 底 —— 不是拜占庭 heritage 下的一个子文化。所以自造 pillar `heritage_moe`
# （原版 pillar 只有 type / is_shown / audio_parameter 三个字段，照
# 00_heritage.txt 的 heritage_byzantine 抄），文化本体的字段照 00_byzantine.txt
# 的 greek 抄：官僚 ethos、希腊语、拜占庭四套 gfx、house_coa 三项。
#
# **传统不能照抄 greek 的 dlc_tradition**：ep3 那 4 条的 is_shown 只认
# `this = culture:greek` / 父文化 / heritage_byzantine，我们的 heritage 不在
# 名单里；fallback 里 roman_legacy / byzantine_succession 同样卡
# heritage_byzantine。只有 formation_fighting（无 is_shown）、legalistic
# （只看信仰教义）、religious_patronage（只有 can_pick）三条对任何 heritage
# 都显示，就用这三条。
# 字段依据原版 00_byzantine.txt（greek）。注意：
#   - 没有 graphical_cultures 字段，图形由 coa/building/clothing/unit_gfx 四项分别指定
#   - head_determination 必填
#   - 文化定义里没有指定 portrait_type 的字段。已确认引擎不支持这条路 ——
#     group 指派的唯一入口是 DNA 的 type 字段（见方案 C 段注释）。
#     文化在这里只作为「哪些角色算二次元」的语义分组，实际换模型靠 effect 赋 DNA。
#   - **ethnicities 指向原版 mediterranean_byzantine**（human group），
#     所以这个文化的男女老少默认全是原版拜占庭外观。只有符合
#     「beauty_good_3 + cosplay 认同传统」的角色才由 on_action 切到 MMD group；
#     候选事件再按角色性别筛选模型。
#     这是用户 2026-08-27 定的设计：文化不是「全员二次元」，而是
#     「绝世美女才二次元」。
#     反过来不能让这里指向 moe ethnicity —— 那会把不符合准入条件的角色也拖进模型 group。
HERITAGE = f"heritage_{CULTURE}"
PILLAR_TXT = f'''# {CULTURE} 文化自己的 heritage（大项）。生成自 tools/build/build_moe_mod.py。
# 形状照原版 00_heritage.txt 的 heritage_byzantine；audio_parameter 沿用
# byzantine（文化界面的音效分组，只有几个枚举值）。
{HERITAGE} = {{
	type = heritage
	is_shown = {{
		heritage_is_shown_trigger = {{
			HERITAGE = {HERITAGE}
		}}
	}}
	audio_parameter = byzantine
}}
'''
w(f"common/culture/pillars/40_{CULTURE}.txt", PILLAR_TXT)

CULTURE_TXT = f'''{CULTURE} = {{
	color = {{ 0.85 0.45 0.75 }}

	# 以东罗马（greek）为底：官僚 ethos、希腊语、拜占庭 gfx；heritage 是自己的。
	ethos = ethos_bureaucratic
	heritage = {HERITAGE}
	language = language_greek
	martial_custom = martial_custom_equal
	head_determination = head_determination_domain

	traditions = {{
		tradition_formation_fighting
		tradition_legalistic
		tradition_religious_patronage
	}}

	name_list = name_list_{CULTURE}

	coa_gfx = {{ byzantine_group_coa_gfx }}
	building_gfx = {{ byzantine_building_gfx }}
	clothing_gfx = {{ byzantine_clothing_gfx }}
	unit_gfx = {{ eastern_unit_gfx }}
	house_coa_frame = house_frame_02
	house_coa_mask_offset = {{ 0.0 0.055 }}
	house_coa_mask_scale = {{ 0.9 0.9 }}

	ethnicities = {{
		100 = {VANILLA_ETHNICITY}
	}}
}}
'''
w(f"common/culture/cultures/01_{CULTURE}.txt", CULTURE_TXT)

# ================================================================ 模型指派规则
# 用户 2026-09-21 更新：文化拥有 cosplay 认同传统，且角色是 beauty_good_3
# 即可用 MMD，未满18岁同样可以。2026-09-24 接入终末地男孩模型后，同一规则覆盖两种性别，
# 每个模型池候选仍按 portrait type 的性别筛选。moe 默认拥有传统，其他文化可采纳。
# 失去资格的 MMD 角色按当前文化恢复 human；无关普通角色保持原样。
#
# 为什么必须走 effect 而不是配置：文化的 `ethnicities` 是**加权随机**列表
# （exe: "Invalid culture ethnicity definition, expected 'weight = ethnicity'"），
# 没有 trigger 维度，表达不出「满足条件才用某个 ethnicity」。而 ethnicity 是
# group 指派的唯一入口。所以条件判断只能放在 effect 里。
#
# `set_ethnicity` **原版从未使用过**（全局 grep 零命中），我们是第一个用它的
# ——它的存在有两条证据：exe 里有 `set_ethnicity` 标识符与 `CSetEthnicityEffect`
# 类，以及那句描述 "Set the ethnicity for this character, if it is of a
# different portrait group and they have DNA it will be randomized"。
# 那句话的后半段正是我们要的：切 group 会重随机 DNA，而我们把 ethnicity 的
# 颜色锁成零宽范围，所以「随机」结果是确定的。
#
# 生命周期与文化变化都需同步，具体钩子统一由 moe_cosplay_rules 生成：
#   on_game_start_after_lobby   开局已存在的角色（含三位书签角色）
#   on_birth_child              新出生的角色（beauty 是先天特质，出生即定）
#   on_birthday                 兜底：后天靠事件获得/失去美貌特质的情况
#
# **反向修正同样必要**：孩子的 ethnicity 会从父母继承，所以不满足条件而又在我们 group 里的
# 角色，要 set_ethnicity 回原版。
# 「已经指派过 MMD 模型」的标记。**必须用自己的 flag，不能用 has_ethnicity**
# —— 那个 trigger 不存在（扫 exe 只有 ethnicity/ethnicities/ethnicity_list/
# set_ethnicity 四个标识符，没有 has_ethnicity）。我曾按直觉写了它，
# limit 里的未知 trigger 不一定报错，会静默让整块判 false。
MOE_FLAG = f"{GROUP}_portrait"


PICK_ACTION = f"{GROUP}_pick_model"
CNT_VAR = f"{GROUP}_pick_n"
PICK_SV = f"{GROUP}_pick_chance"

# ---------------------------------------------------------------- 换 cos 服
# 用户 2026-08-28 第 3 项：右击符合条件的角色 → 交互「更换cos服」（免费）
# → 弹窗选模型 / 选待机姿势。
COS_COST = 0
COS_INT = f"{GROUP}_cosplay_interaction"
COS_EVT = f"{GROUP}_cosplay"

# ---- 大窗口（用户 2026-08-28 第 4 项）
#
# **槽位数 = 实际有模型的角色数**，不能凭空放大。原先写 12 是我凭直觉定的，
# 而只有 3 个填得上，于是 GUI 引用 12 张预览图、9 张不存在，每张一条
#     Could not find texture ... gfx/interface/moe_preview/moe_slot_N.dds not found
# 隐藏的按钮照样会去取贴图，所以「不可见」不能省掉这些报错。
#
# 这个数**只在这里定义一份**（build_moe_cosplay_gui.py 与
# build_moe_previews.py 都从这里读），三处各存一份必然漂。
# 加角色时：这里 +1，并同步 TYPES / MOE_CHARACTERS / moe_out.SLOT_ORDER 与 GAME_OF。
SLOT_COUNT = len(X.CHARS)        # 含未就绪角色：槽位号是登记表序号，不随就绪集合漂移
VAR_OPEN = f"{GROUP}_cos_open"
SLOT_VAR = f"{GROUP}_slot"          # 玩家变量 <SLOT_VAR>_<i>，子 mod 写
SLOT_CHAR_VAR = f"{GROUP}_slot_char"  # 玩家变量 <SLOT_CHAR_VAR>_<i> = 该槽位的角色本人（gui 左栏实时肖像的 datacontext）
PICK_SLOT_ACTION = f"{GROUP}_pick_slot"
# 大厅期把槽号登记到**选中角色**身上（root）。on_game_start_after_lobby 在大厅里
# 还没发生，玩家身上没有 <SLOT_VAR>_<i>；大厅那份窗口以选中角色为根读这些变量。
SLOT_REG_ACTION = f"{GROUP}_register_slots"
CYCLE_SKIN_ACTION = f"{GROUP}_cycle_skin"   # 皮肤轮询分发点（核心留空，子 mod 用 events 追加）

# 姿势常量 POSES / POSE_FLAG_PREFIX 定义在 animations.txt 补丁段之前（那段要用它们，
# 而 Python 顺序执行）。别挪回这里。


def _trig(ind):
    """「该用 MMD 模型」的判据。"""
    return CR.eligibility(ind)

# 三位书签角色的固定指派**不在这里**，在 history/characters 的 dated
# effect 块里（最早执行、确定性）。曾经在 on_game_start_after_lobby 里用
# `character:<id> ?= {}` 又写了一遍，那份连带把原版整块 effect 顶掉了。

EVT = f"{GROUP}_assign"

# The normal builder owns cultural tradition, lifecycle and shared eligibility.
for _rules_path, _rules_text in CR.files(GAME).items():
    w(_rules_path, _rules_text)
for _rules_path, _rules_text in W.core_files(GAME).items():
    w(_rules_path, _rules_text)
for _rules_path, _rules_text in W.files(GAME).items():
    w(_rules_path, _rules_text, mod=O.COLLECTION)
w('descriptor.mod', '''version="0.1.0"
name="Moe Outfits - Collection"
supported_version="1.18.*"
tags={ "Gameplay" "Events" }
dependencies={ "Moe Portraits - Core" }
''', mod=O.COLLECTION)
# 两个自定义传统（cosplay认同、类人异族雇佣）的定义、加成、图标与本地化都归可选文化包
# moe_cultural_units（用户 2026-09-27），由 tools/build/build_moe_cultural_units.py 生成；
# 核心不引用任何自定义传统或其参数。

# Model packs register their own choices; eligibility belongs to the core.
w(f"common/script_values/40_{GROUP}_pool.txt", f"{PICK_SV} = {{ value = 100 divide = var:{CNT_VAR} }}\n")

# ================================================================ 模型池注册
# **每套模型一对文件，归属该模型的子 mod**（用户 2026-08-27 第 4 项：模型子
# mod 只往池子里加模型，不含机制）。核心侧只有一个空的 on_action 分发点。
#
# 文件名带 `_<角色键>` 后缀，写出时按角色直接指定子 mod，后缀只是给人看的，对应来源游戏的
# 模型 mod —— 跟 portrait_types / ethnicities / genes 用的是同一套约定。
#
# 水塘抽样：第 k 个候选以 1/k 概率覆盖前面的选择，结果是每套模型等概率
# 1/n，与装了几个子 mod、装载顺序都无关。`chance = 100` 配 modifier 的写法
# 依据引擎帮助文本 `random = { chance = X modifier = Y effects... }`，
# `chance` 是百分数，所以 1/k 写成 `100 / k`。
REG_NS = f"{GROUP}_pool"


def _pool_reg_event(idx, ck, eth, name_cn, sex):
    """一套模型的注册事件。idx 从 1 起，仅用于事件号。"""
    return f'''# {name_cn} 的模型池注册。生成自 tools/build/build_moe_mod.py。
#
# 挂在核心的 `{PICK_ACTION}` 分发点上（见同目录 on_action 文件）。
# root scope 是待指派的角色，`{CNT_VAR}` 已由核心置 0。
namespace = {REG_NS}_{ck}

{REG_NS}_{ck}.000{idx} = {{
\ttype = character_event
\thidden = yes
\ttrigger = {{ {CR.ELIGIBLE} = yes is_{sex} = yes }}

\timmediate = {{
\t\t# 候选计数 +1。这一步无条件做，哪怕最后没被抽中 —— 计数是
\t\t# 水塘抽样的分母，漏加会让后面的候选概率偏大。
\t\tchange_variable = {{ name = {CNT_VAR} add = 1 }}

\t\t# 以 1/k 的概率认领（k = 当前计数）。第 1 个候选 100%，第 2 个
\t\t# 50%，第 3 个 33%……最终每套模型都是 1/n。
\t\t#
\t\t# chance 用命名 script_value（{PICK_SV}），跟原版
\t\t# `chance = reincarnation_chance` 同一形状。**不写成 modifier 里嵌
\t\t# 内联 script_value 块** —— 原版 128 个 random+modifier 实例里
\t\t# `add`/`factor` 全是字面数字，没有一处嵌块，别赌没人验证过的写法。
\t\trandom = {{
\t\t\tchance = {PICK_SV}
\t\t\tset_ethnicity = {eth}
\t\t\tset_variable = {{ name = moe_current_form value = {idx} }}
\t\t\tadd_character_flag = {MOE_FLAG}
\t\t\tadd_character_flag = {GROUP}_portrait_lineage
\t\t\tremove_character_flag = {GROUP}_portrait_human_checked
\t\t\tremove_character_flag = {GROUP}_portrait_assignment_pending
\t\t\tdebug_log = "MOE_ASSIGN {ck}"
\t\t}}
\t}}
}}
'''


def _pool_reg_action(idx, ck, name_cn):
    """把注册事件挂到核心分发点上的 on_action 追加文件。

    idx 必须与 `_pool_reg_event` 用的是同一个，否则挂上去的是不存在的事件号，
    表现为 `Event ... is orphaned` 或该模型永远不被抽中。
    """
    return f'''# {name_cn} 往模型池注册 —— on_action 追加。
# 生成自 tools/build/build_moe_mod.py。
#
# **必须用 `events`（列表字段）**，多个模型子 mod 各自追加会合并。用
# `effect`（单值字段）会让子 mod 互相顶掉，只剩最后加载的那个生效。
{PICK_ACTION} = {{
\tevents = {{
\t\t{REG_NS}_{ck}.000{idx}
\t}}
}}
'''


for _t, _eth in ETHNICITIES.items():
    _ck = _t[len(f"{GROUP}_"):]
    _i = X.BY_CK[_ck]["slot"]        # 槽位号 = 登记表序号（稳定），也当事件号
    _cn = X.BY_CK[_ck]["name_cn"]
    # 槽位角色 = 专用占位（开局一刻被 0001 指派后杀死，见 history 段）。可玩者也另派一个（slot_char_id），
    # 不再直接指向本尊：本尊换模型/衰老/死亡都会体现在预览格里（用户 2026-09-07 确认）。
    _cid = X.BY_CK[_ck]["slot_char_id"]
    _me = X.BY_CK[_ck]
    _sex = _me.get("sex", "female")
    # 手动认领（0002）走「忽略性别」开关感知的 trigger；随机池注册事件仍写死 is_<sex>。
    _sex_trigger = f"{CR.sex_ok(_sex)} = yes"
    for _wardrobe_path, _wardrobe_text in W.pack_files(_me).items():
        w(_wardrobe_path, _wardrobe_text, mod=O.models_of(_ck))
    _skins = [c for c in ACTIVE if c.get("skin_of") == _ck]     # 挂在本体这一格上轮换的皮肤（登记表顺序）
    _n_skins = 1 + len(_skins)
    # 非可玩者的 ethnicity 与死亡在这里做（history dated effect 对开局前已死者不执行，2026-09-06 实机）。
    _dead_fx = (
        f"\t\t# 非可玩的槽位角色：开局一刻指派 ethnicity 后立刻死亡（用户 2026-09-06 定「生成即死亡」）。\n"
        f"\t\t# history 里没给 beauty_good_3，开局扫描不会碰她；没写死亡，因为 dated effect 对已死者不跑。\n"
        f"\t\tcharacter:{_cid} ?= {{\n"
        f"\t\t\tif = {{\n"
        f"\t\t\t\tlimit = {{ is_alive = yes }}\n"
        f"\t\t\t\tset_ethnicity = {_eth}\n"
        f"\t\t\t\tadd_character_flag = {MOE_FLAG}\n"
        f"\t\t\t\tdeath = {{ death_reason = death_natural_causes }}\n"
        f"\t\t\t\tdebug_log = \"MOE_SLOT dead {_i} {_ck} -> {_eth}\"\n"
        f"\t\t\t}}\n"
        f"\t\t}}\n")
    _lobby_eth = (
        f"\t\t# 大厅期只指派 ethnicity 不杀（让左栏能预览她；开局 0001 再杀）。\n"
        f"\t\tcharacter:{_cid} ?= {{ if = {{ limit = {{ is_alive = yes }} set_ethnicity = {_eth} }} }}\n")
    w(f"events/{REG_NS}_{_ck}.txt", _pool_reg_event(_i, _ck, _eth, _cn, _sex), mod=O.models_of(_ck))
    w(f"common/on_action/zz_{GROUP}_pool_reg_{_ck}.txt",
      _pool_reg_action(_i, _ck, _cn), mod=O.models_of(_ck))
    # 皮肤不单独占格（用户 2026-09-06）：不写槽位事件，靠本体格的轮换按钮切；池注册照做。
    if _me.get("skin_of"):
        continue
    # 皮肤轮换事件（只有带皮肤的本体才有）：root 是被换装的角色，下标 {GROUP}_skin_<槽> 挂在她身上
    # （0 = 本体，1.. = 皮肤，按登记表顺序）。var 比较与 change_variable add 的写法有原版先例
    # （tour_success_var >= 100 / add = var:odds_win_a）。
    _cycle_ev = "" if not _skins else f'''
# Legacy skin dispatcher: collection validation lives on the wardrobe actor.
{GROUP}_slot_{_ck}.0004 = {{
    type = character_event
    hidden = yes
    trigger = {{ {CR.ELIGIBLE} = yes }}
    immediate = {{
        if = {{ limit = {{ var:{GROUP}_want_slot = {_i} exists = scope:moe_wardrobe_actor }}
            scope:moe_wardrobe_actor = {{ moe_cycle_owned_{_i}_effect = yes }}
        }}
    }}
}}
'''
    _cycle_oa = "" if not _skins else f'''
{CYCLE_SKIN_ACTION} = {{
\tevents = {{
\t\t{GROUP}_slot_{_ck}.0004
\t}}
}}
'''
    # ---- 大窗口的槽位注册（第 4 项）。**归模型子 mod**：占哪个槽、预览图长
    #      什么样，都是模型自己的事，核心只有窗口骨架。
    w(f"events/{GROUP}_slot_{_ck}.txt", f'''# {_cn} 占用大窗口的第 {_i} 号槽位。
# 生成自 tools/build/build_moe_mod.py。
#
# 两件事：
#   1. 开局在玩家身上置 `{SLOT_VAR}_{_i}` —— 大窗口靠它决定第 {_i} 格显不显示
#      （gui 侧 `GetPlayer.MakeScope.Var('{SLOT_VAR}_{_i}')`，这是原版
#      hud.gui 验证过的读法）。
#   2. 玩家点了第 {_i} 格时认领，把目标角色切到本模型。
#
# 预览图由本子 mod 提供，路径固定：
#     gfx/interface/{GROUP}_preview/{GROUP}_slot_{_i}.dds
namespace = {GROUP}_slot_{_ck}

# 开局占槽。挂 on_game_start_after_lobby（scopeless，所以 scope = none +
# every_player 转发 —— 照原版 debug_portraits.1 的形状）。
{GROUP}_slot_{_ck}.0001 = {{
\tscope = none
\thidden = yes
\timmediate = {{
\t\tevery_player = {{
\t\t\tset_variable = {{ name = {SLOT_VAR}_{_i} value = 1 }}
\t\t\tset_variable = {{ name = {GROUP}_skins_{_i} value = {_n_skins} }}   # 换肤小按钮 visible 读它 > 1
\t\t\t# 左栏实时肖像的 datacontext：槽位角色本人。character:ID 不存在时 set_variable 会报错，先判 exists。
\t\t\tif = {{
\t\t\t\tlimit = {{ exists = character:{_cid} }}
\t\t\t\tset_variable = {{ name = {SLOT_CHAR_VAR}_{_i} value = character:{_cid} }}
\t\t\t}}
\t\t\telse = {{ debug_log = "MOE_SLOT char {_cid} missing ({_ck})" }}
\t\t}}
{_dead_fx}\t}}
}}

# 认领。root 是被换装的角色（scripted_gui 里 `var:{GROUP}_cos_target` 指到它）。
{GROUP}_slot_{_ck}.0002 = {{
\ttype = character_event
\thidden = yes
\ttrigger = {{ {CR.ELIGIBLE} = yes {_sex_trigger} }}
\timmediate = {{
\t\tif = {{ limit = {{ var:{GROUP}_want_slot = {_i} exists = scope:moe_wardrobe_actor }}
\t\t\tscope:moe_wardrobe_actor = {{ moe_wear_{_ck}_effect = yes }}
\t\t}}
\t}}
}}

# 大厅期登记：root 是大厅里点了「更换cos服」的选中角色（核心 `{SLOT_REG_ACTION}`）。
# 与 0001 写的是同一个变量，只是挂在角色自己身上 —— 大厅窗口以她为根读。
{GROUP}_slot_{_ck}.0003 = {{
\ttype = character_event
\thidden = yes
\timmediate = {{
\t\tset_variable = {{ name = {SLOT_VAR}_{_i} value = 1 }}
\t\tset_variable = {{ name = {GROUP}_skins_{_i} value = {_n_skins} }}
\t\tif = {{
\t\t\tlimit = {{ exists = character:{_cid} }}
\t\t\tset_variable = {{ name = {SLOT_CHAR_VAR}_{_i} value = character:{_cid} }}
\t\t}}
{_lobby_eth}\t\tdebug_log = "MOE_SLOT register {_i} {_ck}"
\t}}
}}
{_cycle_ev}''', mod=O.models_of(_ck))
    w(f"common/on_action/zz_{GROUP}_slot_{_ck}.txt", f'''# {_cn} 的槽位挂钩。生成自 tools/build/build_moe_mod.py。
#
# 两个 on_action 都用 `events`（列表字段，跨 mod 合并）。用 `effect` 会让
# 多个模型子 mod 互相顶掉，只剩最后加载的那个生效。
on_game_start_after_lobby = {{
\tevents = {{
\t\t{GROUP}_slot_{_ck}.0001
\t}}
}}

{PICK_SLOT_ACTION} = {{
\tevents = {{
\t\t{GROUP}_slot_{_ck}.0002
\t}}
}}

{SLOT_REG_ACTION} = {{
\tevents = {{
\t\t{GROUP}_slot_{_ck}.0003
\t}}
}}
{_cycle_oa}''', mod=O.models_of(_ck))

# ================================================================ 换 cos 服
# 用户 2026-08-28 第 3 项。右击符合条件的角色 → 交互「更换cos服」。
# 换装和姿势切换始终免费；可选的服装获取 Mod 另行处理申请同款、裁缝
# 委托与朝圣奖励。主包默认允许所有已安装模型，随机生成角色仍按模型池自然分配。
#
# **为什么用 event 弹窗而不是手写 .gui 窗口**：CK3 的 event 本身就是弹窗
# UI，选项由脚本生成、引擎负责排版，且能被本地化。手写 .gui 要自己搭
# vbox/按钮/datacontext，而 gui 出错**不进 error.log 只进 gui_warnings.log**，
# 且经常是「窗口打不开」这种没有任何提示的失败。事件路线是引擎保证能渲染的。
#
# **姿势清单归核心、模型清单归子 mod**：换模型走 on_action 分发（子 mod 自
# 注册，核心不知道有哪些模型）；换姿势读 POSES（animations.txt 单文件覆盖，
# 只能核心持有）。这个不对称是引擎语义决定的。
COS_INT_TXT = f'''# Free wardrobe; acquisition is handled separately.
{COS_INT} = {{
    category = interaction_category_friendly
    icon = designate_favorite
    interface_priority = 50
    desc = {COS_INT}_desc
    auto_accept = yes
    is_shown = {{
        scope:actor = {{ is_ai = no }}
        scope:recipient = {{ has_character_flag = moe_portrait {CR.ELIGIBLE} = yes }}
    }}
    is_valid_showing_failures_only = {{
        scope:recipient = {{
            {CR.ELIGIBLE} = yes
            custom_tooltip = {{ text = moe_wardrobe_target_tt OR = {{ this = scope:actor AND = {{ exists = employer employer = scope:actor }} }} }}
        }}
    }}
    on_accept = {{
        scope:actor = {{
            remove_variable = moe_wardrobe_lobby
            moe_refresh_wardrobe_effect = yes
            moe_clear_wardrobe_availability_effect = yes
            trigger_event = {{ on_action = moe_register_wardrobe }}
            set_variable = {{ name = {GROUP}_cos_target value = scope:recipient }}
            set_variable = {{ name = {VAR_OPEN} value = 1 }}
        }}
    }}
}}
'''
w(f"common/character_interactions/40_{GROUP}_cosplay.txt", COS_INT_TXT)

# ---------------------------------------------------------------- scripted_gui
# 大窗口的按钮回调。每个按钮一个 scripted_gui，`.gui` 里用
# `datacontext = "[GetScriptedGui('名')]"` + `onclick = "[ScriptedGui.Execute(...)]"`
# 绑定 —— 照原版 `window_faith.gui` 的 `create_head_of_faith` 按钮。
_sg = [f"""# 换 cos 服大窗口的按钮回调。生成自 tools/build/build_moe_mod.py。
#
# `scope` 是 character（gui 侧传的是 `GetPlayer.MakeScope`）。
# 目标角色存在玩家的 `{GROUP}_cos_target` 变量里（交互的 on_accept 写的）。

# 关窗
{GROUP}_close_cosplay = {{
\tscope = character
\teffect = {{
\t\tremove_variable = {VAR_OPEN}
\t}}
}}

# 大厅入口（gui/multiplayer_lobby.gui 里注入的按钮，见 build_moe_cosplay_gui.py）。
# root = 大厅选中的角色。大厅期没有 GetPlayer，所以目标与显示标志都写在她自己身上，
# 大厅那份窗口副本也以她为根读变量 —— 后面的 pick_slot / set_pose / close 全部复用。
# 资格判定与模型指派用同一条规则（is_shown 不满足时按钮直接不显示）。
# 槽位登记：子 mod 在 {SLOT_REG_ACTION} 上把自己的槽号写到 root 身上。
{GROUP}_open_cosplay_self = {{
\tscope = character
\tis_shown = {{
\t\texists = this   # 大厅未选中角色时 root 是空角色，先挡住（2026-09-06 实机 125 条报错）
{_trig(2)}
\t}}
\tis_valid = {{
\t\texists = this
{_trig(2)}
\t}}
\teffect = {{
\t\tset_variable = {{ name = moe_wardrobe_lobby value = 1 }}
\t\tmoe_clear_wardrobe_availability_effect = yes
\t\ttrigger_event = {{ on_action = moe_register_wardrobe }}
\t\tset_variable = {{ name = {GROUP}_cos_target value = root }}
\t\tset_variable = {{ name = {VAR_OPEN} value = 1 }}
\t\ttrigger_event = {{ on_action = {SLOT_REG_ACTION} }}
\t\tdebug_log = "MOE_COS lobby_open"
\t}}
}}
"""]

# 模型槽位：点第 i 个槽 → 把槽号存下来 → 广播给子 mod 认领。
# **核心不知道第 i 槽是哪套模型** —— 与随机池同一个思路，只是这里是确定性
# 选择而不是水塘抽样。
for _i in range(1, SLOT_COUNT + 1):
    _form = next(c for c in X.CHARS if c['slot'] == _i)
    _sex_trigger = f"{CR.sex_ok(_form.get('sex', 'female'))} = yes"
    _sg.append(f'''
{GROUP}_pick_slot_{_i} = {{
    scope = character
    is_valid = {{
        has_variable = {SLOT_VAR}_{_i}
        exists = var:{GROUP}_cos_target
        var:{GROUP}_cos_target = {{ {CR.ELIGIBLE} = yes {_sex_trigger} }}
        custom_tooltip = {{ text = moe_wardrobe_locked_tt moe_can_wear_{_form['ck']} = yes }}
    }}
    effect = {{ moe_refresh_wardrobe_effect = yes moe_wear_{_form['ck']}_effect = yes }}
}}
{GROUP}_cycle_skin_{_i} = {{
    scope = character
    is_valid = {{
        has_variable = {GROUP}_skins_{_i}
        exists = var:{GROUP}_cos_target
        var:{GROUP}_cos_target = {{ {CR.ELIGIBLE} = yes {_sex_trigger} }}
        moe_has_owned_cycle_{_i} = yes
    }}
    effect = {{ moe_refresh_wardrobe_effect = yes moe_cycle_owned_{_i}_effect = yes }}
}}
''')

# 姿势：在目标角色上维护一组互斥 character flag（姿势清单归核心，见 POSES）。
#
# portrait animation 的 weight 虽然拿到 Character scope，却不支持通用变量
# trigger：实机每次求值都会报 `has_variable ... scope doesn't support variables`。
# 原版同一上下文用 `has_character_flag = generated_scribe`，所以这里沿用该契约。
# `idle` 只清 flag，不新增 flag；没有姿势 flag 就回到原版默认待机。
_pose_flags = [f"{POSE_FLAG_PREFIX}{k}" for k, *_rest in POSES if k != "idle"]
# 252 个姿势 × 251 条 remove_character_flag 会是 6 万行，抽成一条 scripted_effect 统一清。
CLEAR_POSE_FX = f"{GROUP}_clear_pose_flags"
w(f"common/scripted_effects/40_{GROUP}_cosplay.txt",
  f"# 清掉全部待机姿势 flag（互斥组）。生成自 tools/build/build_moe_mod.py。\n"
  f"{CLEAR_POSE_FX} = {{\n"
  + "".join(f"\tremove_character_flag = {flag}\n" for flag in _pose_flags)
  + "}\n")
for _k, _st, _cn, _en, _pk in POSES:
    _set = ("" if _k == "idle"
            else f"\n\t\t\tadd_character_flag = {POSE_FLAG_PREFIX}{_k}")
    _sg.append(f"""
{GROUP}_set_pose_{_k} = {{
\tscope = character
\tis_valid = {{
\t\texists = var:{GROUP}_cos_target
\t\tvar:{GROUP}_cos_target = {{ {CR.ELIGIBLE} = yes }}
\t}}
\teffect = {{
\t\tvar:{GROUP}_cos_target ?= {{
\t\t\t{CLEAR_POSE_FX} = yes{_set}
\t\t\tdebug_log = "MOE_POSE {_k}"
\t\t}}
\t}}
}}
""")
w(f"common/scripted_guis/40_{GROUP}_cosplay.txt", "".join(_sg))

# 槽位分发点。**故意留空**，与模型池的 `{PICK_ACTION}` 同一个模式：
# 模型子 mod 往它的 events 列表追加自己的认领事件，比对 `{GROUP}_want_slot`
# 是否等于自己占的槽号。
w(f"common/on_action/zz_{GROUP}_pick_slot.txt",
  f'''# 大窗口选模型的分发点。生成自 tools/build/build_moe_mod.py。
#
# **故意留空** —— 核心不知道哪个槽位是哪套模型。模型子 mod 这样接：
#
#     {PICK_SLOT_ACTION} = {{
#         events = {{ my_pack_slot.0001 }}
#     }}
#
# 事件里判 `var:{GROUP}_want_slot = <自己的槽号>`，命中就 set_ethnicity。
# 用 `events`（列表字段，跨 mod 合并），**不要用 `effect`**（单值，会互相顶掉）。
{PICK_SLOT_ACTION} = {{
}}

# 大厅期槽位登记。root = 大厅里被选中并点了「更换cos服」的角色。模型子 mod 在这里
# 用 character_event 把 `{SLOT_VAR}_<自己的槽号>` 写到 root 身上（开局那份写在玩家
# 身上，大厅期还没发生）。同样故意留空，用 `events` 追加。
{SLOT_REG_ACTION} = {{
}}

# 皮肤轮询分发点（2026-09-06）。root = 被换装的角色，`var:{GROUP}_want_slot` 是被点的槽号。
# 子 mod 在这里接 character_event，自己维护 `{GROUP}_skin_<槽号>` 下标并切换。同样故意留空。
{CYCLE_SKIN_ACTION} = {{
}}
''')


# 换 cos 服**不再走事件弹窗**（2026-08-29）。用户 2026-08-28 要求换成一个能
# 预览所有模型、姿势也在下拉里的大窗口，于是交互的 `on_accept` 改成置
# `moe_cos_open` 变量、由 `gui/moe_cosplay_window.gui` 显示。
# 原来的两级事件（`moe_cosplay.0001` 主菜单 / `.0002` 姿势）就没有任何触发者，
# 实机报 `[E][jomini_eventmanager.cpp:371]: Event moe_cosplay.0001 is orphaned`。
#
# **本地化键要保留**：大窗口的标题与九个姿势按钮仍读 `moe_cosplay.0001.t` /
# `moe_cosplay.0002.<键>`（loc 与事件是两套文件，删事件不影响 loc）。

# ================================================================ 开局说明弹窗
# 用户 2026-08-28 第 6 项：选完角色正式进入游玩后弹窗，说明①什么条件的角色
# 能拿到 MMD 模型 ②符合条件的角色可以右键 + GUI 换立绘与姿势。
#
# **挂 `on_game_start_after_lobby` 的 `events`，不是 `effect`**：
#   · `effect` 是单值字段，会把原版那一整块顶掉（连带 21 条
#     `Event X is orphaned`）—— 这个坑我在指派逻辑上踩过一次。
#   · 那个 on_action 是 **scopeless** 的（没有 root character），所以
#     `character_event` 挂不上去。但 `events` 列表里放**scopeless 的
#     `letter_event` / `character_event` 都不行** —— 需要一个能自己找到玩家的
#     入口。
#
# 实现：挂在 `on_game_start_after_lobby` 上，事件类型用 `type = country_event`
# 里没有对应概念，CK3 只有 character/letter/court 几种。所以改用一个中转：
# on_action 的 events 列表挂一个 **scopeless 事件不可行** → 退回用
# `on_birthday` 之外的路径不合适（要等一年）。
#
# 最终做法：挂 `on_game_start_after_lobby`，事件本体用 `scope = none` 起步、
# 在 immediate 里 `every_player = { trigger_event = ... }` 把弹窗投给每个玩家。
# 这是原版教程/DLC 提示用的同一套路径（原版 `tutorial.0001` 就挂在这里）。
GS_EVT = f"{GROUP}_welcome"
_pose_list_cn = (f"共 {len(POSES)} 种，按" + "、".join(g for g, _e, _i in POSE_GROUPS) + "分组")
GS_ON_ACTION = f'''# 开局说明弹窗 —— on_action 挂钩。生成自 tools/build/build_moe_mod.py。
#
# **用 `events`（列表字段）不用 `effect`（单值字段）**：`effect` 会把原版
# 这一整块顶掉，实测连带 21 条 `Event X is orphaned`。
on_game_start_after_lobby = {{
\tevents = {{
\t\t{GS_EVT}.0001
\t}}
}}
'''
w(f"common/on_action/zz_{GROUP}_welcome.txt", GS_ON_ACTION)

GS_EVT_TXT = f'''# 开局说明弹窗。生成自 tools/build/build_moe_mod.py。
#
# `on_game_start_after_lobby` 是 **scopeless** 的，所以 0001 只做转发：
# 用 `every_player` 找到每个玩家角色，再给他们各弹一次 0002。
# 直接把 0002 挂 on_action 会因为没有 root character 而不触发（零报错）。
namespace = {GS_EVT}

# **不写 `type`**，只写 `scope = none` + `hidden = yes` —— 照原版
# `debug_portraits.1` 和 `fp1_scandinavian_adventurers.0004` 的形状，它们就是
# 挂在 scopeless 的 `on_game_start` 上做转发的。写 `type = character_event`
# 会因为没有 root character 而不触发，且**零报错**。
{GS_EVT}.0001 = {{
\tscope = none
\thidden = yes

\timmediate = {{
\t\t# **这条标记不能省**：原版在 `on_game_start_after_lobby` 上只用过
\t\t# `effect`，没有用 `events` 的先例，所以「这个 on_action 支不支持
\t\t# events 列表」是未经验证的。没有标记时事件不触发和触发成功在日志里
\t\t# 长得一模一样（都是只有一行 `Loaded [2] events`）。
\t\tdebug_log = "MOE_WELCOME dispatch"
\t\tevery_player = {{
\t\t\tdebug_log = "MOE_WELCOME to_player"
\t\t\ttrigger_event = {GS_EVT}.0002
\t\t}}
\t}}
}}

# 正式弹窗。**不延迟**（2026-09-05 改）：原先写 `days = 1` 想避开开局那批
# 原版弹窗，但延迟事件要游戏时间前进一天才触发，而玩家开局是暂停的 ——
# 用户开局先看角色、点交互，从来没走到第二天，表现就是「弹窗不生效」。
# 日志证据：`MOE_WELCOME to_player` 有，之后 43 分钟没有任何日期推进的
# 记录（无 MOE_ASSIGN、无生日事件）。立即触发的事件在暂停时也会弹。
{GS_EVT}.0002 = {{
\ttype = character_event
\ttitle = {GS_EVT}.0002.t
\tdesc = {GS_EVT}.0002.desc
\ttheme = family

\toption = {{
\t\tname = {GS_EVT}.0002.ok
\t}}
}}
'''
w(f"events/{GS_EVT}.txt", GS_EVT_TXT)

# 名字既进 name_list 也要进本地化，否则每个名字报一条
#   [E][culture_name_equivalency.cpp:101]: Missing loc Sakura: "Sakura"
# 所以在这里定义一次，name_list 和 loc 都从它派生，不手写两遍。
#
# **开场人物的名字必须在列**，否则报
#   [E][characterhistory.cpp:807]: Missing loc for name 'Aglina' for character
# 所以从 MOE_CHARACTERS 并进来，不靠手写记得（手写那版漏了 Aglina）。
_MODEL_MALE_NAMES = {_c["name_en"] for _c in X.CHARS if _c.get("sex") == "male"}
MALE_NAMES = sorted({"Adalbert", "Berthold", "Conrad", "Dietrich", "Ernst"} | _MODEL_MALE_NAMES)
FEMALE_NAMES = sorted({
    "Florence", "Asteria", "Faelynn", "Cheyanne", "Sakura",
    "Perlica", "Laurentina", "Fluorite", "Eldera", "Baizhi",
    "Yangyang", "Rover",
} | {_c["name_en"] for _c in X.CHARS if _c.get("sex", "female") == "female"})
# 每个开场人物的家族名都要在这里，否则报
#   [E][dynasty_template.cpp:60]: Missing name for dynasty moe_dynasty_aglina
# **必须从 MOE_CHARACTERS 派生**，不能手写 —— 手写那版漏了 dynn_Aglina，
# 加第三个角色时没人想到还要回来补这张表。多出来的名字（阿斯缇亚、芙铃
# 暂无角色）无害，留着备用。
DYNASTY_NAMES = sorted({_c["dyn_name"] for _c in MOE_CHARACTERS.values()}
                       | {"dynn_Asteria", "dynn_Faelynn"})

# 中文译名。没列到的回落到英文原名。
NAME_CN = {
    "Florence": "芙洛伦", "Asteria": "阿斯缇亚", "Faelynn": "芙铃",
    "Cheyanne": "夏安", "Sakura": "樱", "Perlica": "珀莉卡",
    "Laurentina": "洛茜", "Fluorite": "萤石", "Eldera": "艾尔德拉",
    "Baizhi": "白芷", "Yangyang": "秧秧", "Rover": "漂泊者",
    "Aglina": "洁尔佩塔",
    "Adalbert": "阿达尔伯特", "Berthold": "贝托尔德", "Conrad": "康拉德",
    "Dietrich": "迪特里希", "Ernst": "恩斯特",
}


def _names_block(names, per_line=5):
    """把名字列表排成 name_list 里的多行缩进块。"""
    rows = [names[i:i + per_line] for i in range(0, len(names), per_line)]
    return "\n".join("\t\t" + " ".join(r) for r in rows)


NAME_LIST = f'''name_list_{CULTURE} = {{
	cadet_dynasty_names = {{
		"dynn_Florence"
	}}

	dynasty_names = {{
{chr(10).join(f'		"{d}"' for d in DYNASTY_NAMES)}
	}}

	male_names = {{
{_names_block(MALE_NAMES)}
	}}

	female_names = {{
{_names_block(FEMALE_NAMES)}
	}}
}}
'''
w(f"common/culture/name_lists/01_{CULTURE}.txt", NAME_LIST)

# ================================================================ 测试角色
# 让 PoC 进游戏就能看到。做法：造一个持有既有伯爵领的角色，挂上 moe DNA。
#
# dna = <dna名> 是 history 角色的标准字段（原版大量使用，如
# history/characters/andalusian.txt: dna = 73957_ibn_marwan）。
# 这就是「玩家可控指派」的静态形态；动态形态用 effect 改 DNA，
# 触发条件可以是特质、宗教、决定、角色互动等任何东西。
# 常量保留给别处引用（外层 .mod 描述、report 等）。
TEST_CHAR_ID = MOE_CHARACTERS["flo"]["char_id"]
TEST_TITLE = MOE_CHARACTERS["flo"]["title"]
TEST_DATE = MOE_START_DATE

# ---- 角色本体
# dna = <dna名> 是 history 角色的标准字段（原版大量使用，如
# history/characters/andalusian.txt: dna = 73957_ibn_marwan）。
# 这就是「玩家可控指派」的静态形态；动态形态用 effect 改 DNA，
# 触发条件可以是特质、宗教、决定、角色互动等任何东西。
_char_blocks = []
for _ck, _cs in _STARTS.items():
    _st = "\n".join(f"\t{k} = {v}" for k, v in _cs["stats"].items())
    _char_blocks.append(f"""# {_cs['name_cn']}。进游戏后在 {_cs['title']} 能看到她。
#
# dna = {_cs['type']} 给她 moe group 的 gene 值。**但 group 归属由
# ethnicity 决定，不由这个字段决定** —— 实际指派在
# common/on_action/40_{GROUP}_portrait_assign.txt 里按 char_id 固定。
#
# trait = beauty_good_3 是准入条件：这个文化只有最高级美貌特质的角色
# 才用 MMD 模型。三位女性书签角色必须带它，否则会被反向修正切回原版外观。
{_cs['char_id']} = {{
\tname = "{_cs['name_en']}"
\tfemale = yes
\tdna = {_cs['type']}
\ttrait = beauty_good_3
\tdynasty_house = {_cs['house']}
\treligion = catholic
\tculture = {CULTURE}

{_st}

\t{_cs['birth']} = {{
\t\tbirth = yes
\t}}
\t# **在 history 里直接指派 ethnicity**，不只靠 on_action。
\t#
\t# 为什么两条路都要：ethnicity 决定 portrait group，而角色实例化时若
\t# DNA 的 group 与 ethnicity 的 group 不一致，引擎会静默重随机 DNA
\t# （回落到 ethnicity 那一侧）。文化的 ethnicities 现在指向原版
\t# {VANILLA_ETHNICITY}（human group），所以她们出生时必然不一致、
\t# 会被打回原版外观 —— 实测就是这个结果（2026-08-27，日志证明是新档、
\t# 角色确实是 Historical ID {_cs['char_id']}，立绘仍是原版）。
\t#
\t# history 的 dated effect 块在「running through history」阶段执行，
\t# 是确定性的；on_action 是否合并同名块这件事我还没验证，不能只靠它。
\t{MOE_START_DATE} = {{
\t\teffect = {{
\t\t\tset_ethnicity = {ETHNICITIES[_cs['type']]}
\t\t\tadd_character_flag = {MOE_FLAG}
\t\t\tdebug_log = "MOE_HISTORY {_cs['char_id']} -> {ETHNICITIES[_cs['type']]}"
\t\t}}
\t}}
\t{_cs['death']} = {{
\t\tdeath = yes
\t}}
}}
""")
if _char_blocks:
    w(f"history/characters/01_{CULTURE}_test.txt", "\n".join(_char_blocks))

# ---- 非可玩角色：生成即死亡（用户 2026-09-06 决定）
# 换装窗口左栏要实时渲染每个槽位的模型，`Character.GetAnimatedPortrait` 需要一个真实角色做
# datacontext（`GetAnimatedBookmarkPortrait` + `AnimationTestWindow` 那条从未验证的路实机不出图）。
# 所以每个模型都要有个角色。不可玩的 20 人不该出现在世界里（会成为可婚配的真实廷臣），
# 于是在 history 里出生 → 指派 ethnicity → 开局前一天死亡。死者不受生日反向修正、开局扫描
# 影响（它们只遍历在世者），也不会被当成宫廷成员。
# **未验证**：死者用显式动画块渲染时引擎是否强制播 dead / 去色，只能进游戏看左栏。
# 归各自模型子 mod：dna 条目定义在那里，核心单独装时不会有悬空 dna。
_NP_BIRTH = "850.1.1"   # 早于最早书签 867，否则 867 开局槽位角色不存在、换装格全空（2026-10-11）
for _c in ACTIVE:
    if _c.get("skin_of"):     # 皮肤不占格，也就不需要左栏肖像用的槽位角色
        continue
    # 可玩者也建一个（slot_char_id ≠ char_id）：预览格不能指向本尊（2026-09-07）。
    _t, _cid = _c["type"], _c["slot_char_id"]
    _sex = _c.get("sex", "female")
    w(f"history/characters/40_{GROUP}_{_c['ck']}.txt", f"""# {_c['name_cn']}（槽位 {_c['slot']}）。生成自 tools/build/build_moe_mod.py。
# 非可玩角色：只为换装窗口左栏的实时肖像存在。**history 里不写 effect 也不写死亡**：
# dated effect 对开局前已死的角色不执行（2026-09-06 实机 20 人 0 条 MOE_HISTORY），
# 所以 ethnicity 指派与死亡都在子 mod 的槽位事件 0001（on_game_start_after_lobby）里做 ——
# 她活到开局这一刻，被指派后立刻死亡，不进任何宫廷。不给 beauty_good_3，开局扫描的资格
# 判定不会碰她，不与随机池抢 ethnicity。
{_cid} = {{
\tname = "{_c['name_en']}"
\t{_sex} = yes
\tdna = {_t}
\treligion = catholic
\tculture = {CULTURE}
\t{_NP_BIRTH} = {{
\t\tbirth = yes
\t}}
}}
""", mod=O.models_of(_c["ck"]))
print(f"  槽位占位角色（开局即死亡）：{sum(1 for _c in ACTIVE if not _c.get('skin_of'))} 个（含 {sum(1 for _c in ACTIVE if _c['playable'])} 个可玩者的专用占位）")


# ---- 王朝与家族：history 角色引用 dynasty_house，必须存在
if _STARTS: w(f"common/dynasties/01_{CULTURE}.txt", "".join(
    f"""{_cs['dynasty']} = {{
\tname = "{_cs['dyn_name']}"
\tculture = {CULTURE}
}}
""" for _cs in _STARTS.values()))

if _STARTS: w(f"common/dynasty_houses/01_{CULTURE}.txt", "".join(
    f"""{_cs['house']} = {{
\tname = "{_cs['dyn_name']}"
\tdynasty = {_cs['dynasty']}
}}
""" for _cs in _STARTS.values()))

# ---- 纹章：不给就报
#   Dynasty 'dynn_X-moe_dynasty_X' had no founder for coat of arms
#   generation, it should have a pre-scripted coat of arms instead
# 引擎的随机纹章生成靠「创始人」推导，而这些角色是凭空插进 history 的，
# 没有父系链，所以必须预置。键名用王朝 ID。
_coa = ['# 预置王朝纹章。缺了会报 "had no founder for coat of arms generation"。\n']
for _cs in _STARTS.values():
    _coa.append(f"""{_cs['dynasty']} = {{
\tpattern = "pattern_solid.dds"
\tcolor1 = "white"
\tcolor2 = "{_cs['coa_color']}"
\tcolored_emblem = {{
\t\t# 原版自带的贴图（已确认文件存在于
\t\t# game/gfx/coat_of_arms/colored_emblems/）。**贴图名写错不报错，
\t\t# 只是纹章渲染成空白**，所以要先确认文件在。
\t\ttexture = "{_cs['emblem']}"
\t\tcolor1 = "{_cs['coa_color']}"
\t\tinstance = {{ position = {{ 0.5 0.5 }} scale = {{ 0.75 0.75 }} }}
\t}}
}}
""")
if _STARTS:
    w(f"common/coat_of_arms/coat_of_arms/40_{CULTURE}.txt", "".join(_coa))

# ---- 持有头衔，否则游戏里找不到（未持地角色不在地图上）
# 领地都在加泰罗尼亚且不在任何原版书签里，不会和书签角色抢地。
if _STARTS: w(f"history/titles/01_{CULTURE}_test.txt", "".join(
    f"""{_cs['title']} = {{
\t{MOE_START_DATE} = {{
\t\tholder = {_cs['char_id']}
\t}}
}}
""" for _cs in _STARTS.values()))

# ---------------------------------------------------------------- 书签
# 把她做成 1066 书签的可玩角色，开局第一屏就能看到并直接选，
# 不用每次进游戏再找巴塞罗那或开控制台 play。
#
# 书签角色块自带 `type = <portrait type>` 字段（原版都写 male/female），
# 这是除 DNA 之外的第二个 portrait type 指派入口 —— 而且书签选择界面
# 本身就渲染 3D 肖像，验收比进游戏快得多。
#
# position 是书签地图上的像素坐标（原版 1066 伊比利亚在 { 640 1000 } 附近）。
#
# **三个角色合进一个 bm_ 块**（2026-08-27，用户要求）。原版 bm_1066_iberia
# 就是这个结构：一个书签块里多个平级 `character = {}`，各自持地、各自
# `difficulty`、各自可玩 —— 界面上是同一个书签里的多张卡片。此前分成三个
# bm_ 块是误判（以为多 character 只用于「同一开局的替代选择」，实际原版
# 加利西亚国王与莱昂国王就是两个独立可玩开局，同在一个块里）。
#
# 书签名沿用 bm_{CULTURE}_flo：贴图与 loc key 都按书签 id 硬编码命名，
# 换 id 要连带改四个文件名，没必要。
BM_ID = f"bm_{CULTURE}_flo"
_bm_chars = []
for _ck, _cs in _STARTS.items():
    _bm_chars.append(f"""\t# {_cs['name_cn']}
\tcharacter = {{
\t\tname = "bookmark_{CULTURE}_{_ck}"
\t\tdynasty_house = {_cs['house']}
\t\tdynasty_splendor_level = 1
\t\ttype = {_cs['type']}
\t\tbirth = {_cs['birth']}
\t\ttitle = {_cs['title']}
\t\tgovernment = feudal_government
\t\tculture = {CULTURE}
\t\treligion = catholic
\t\tdifficulty = "BOOKMARK_CHARACTER_DIFFICULTY_EASY"
\t\thistory_id = {_cs['char_id']}
\t\t# 书签地图上的像素坐标。参照原版 bm_1066_iberia：加利西亚 450,530、
\t\t# 莱昂 620,180。**写超范围不报错，卡片直接渲染到画面外。**
\t\tposition = {{ {_cs['position']} }}

\t\tanimation = personality_content
\t}}
""")

_bm_blocks = [f"""{BM_ID} = {{
\tstart_date = {MOE_START_DATE}
\tis_playable = yes
\tgroup = bm_group_1066

\tweight = {{
\t\tvalue = {MOE_CHARACTERS['flo']['bm_weight']}
\t}}

{"".join(_bm_chars)}}}
"""]

BOOKMARK = f"""# 二次元肖像的开局入口。
#
# 书签选择界面直接渲染 3D 肖像，是最快的验收点：
# 不用进游戏、不用找地图、不用开控制台。
#
# 角色块的 type 字段就是 portrait type，与 DNA 里的 type 同义。
{"".join(_bm_blocks)}"""
if _STARTS:
    w(f"common/bookmarks/bookmarks/40_{CULTURE}.txt", BOOKMARK)

# 书签角色的肖像数据。缺了报
#   Bookmark character 'X' has no portrait in database
# **文件名必须等于书签角色名**，内容格式与 dna_data 的 portrait_info 同源，
# 但多了 id / random_seed / age 三个字段（原版都是从游戏内导出的）。
#
# 这是第三个 portrait type 指派入口（前两个是 dna_data 的 portrait_info
# 和书签角色块的 type 字段），同样只认本 group 的 type 名。
# 书签关闭时整段不写：槽位别名 bookmark_{GROUP}_slot_<i> 已无人引用（换装格改用
# Character.GetAnimatedPortrait，gui 里 GetAnimatedBookmarkPortrait 只剩注释）。
for _ck, _cs in (ALL_CHARS if _STARTS else {}).items():
    _bp = f"""# 书签角色肖像。type 指向 moe group，gene 集与 common/genes/ 里声明的一致。
bookmark_{CULTURE}_{_ck}={{
\ttype={_cs['type']}
\tid={_cs['char_id']}
\trandom_seed={1234567 + _cs['char_id'] % 1000}
\tage=0.30
\tgenes={{
\t\thair_color={{ 137 167 111 140 }}
\t\tskin_color={{ 46 75 44 75 }}
\t\teye_color={{ 195 192 123 200 }}
\t}}
}}
"""
    w(f"common/bookmark_portraits/bookmark_{CULTURE}_{_ck}.txt", _bp, mod=O.models_of(_ck))
    # **同一份肖像再按槽位号存一份别名**（2026-09-05，换装窗口的实时预览）。
    # 核心 gui 用 `GetAnimatedBookmarkPortrait('bookmark_{GROUP}_slot_<i>', ...)`
    # 渲第 i 格的模型 —— 核心不知道第 i 格是谁，所以文件名只能按槽位号约定，
    # 与预览图 `{GROUP}_slot_<i>.dds` 是同一条契约。槽位号 = ETHNICITIES 的
    # 顺序（与 events/{GROUP}_slot_<ck>.txt 里写玩家变量的号一致）。
    _slot_i = X.BY_CK[_ck]["slot"]
    w(f"common/bookmark_portraits/bookmark_{GROUP}_slot_{_slot_i}.txt",
      _bp.replace(f"bookmark_{CULTURE}_{_ck}=", f"bookmark_{GROUP}_slot_{_slot_i}="),
      mod=O.models_of(_ck))

# 书签的两张贴图。缺了报
#   Bookmark 'bm_X' background 'gfx/interface/bookmarks/bm_X.dds' not found
#   Bookmark character 'X' location image '...bm_X_<角色名>.dds' not found
# 命名规则是引擎硬编码的：
#   背景     gfx/interface/bookmarks/<书签id>.dds
#   角色定位 gfx/interface/bookmarks/<书签id>_<角色名>.dds
# 这只是 UI 装饰，不影响 3D 肖像。直接借原版伊比利亚书签的图当占位
# （从游戏目录实时复制，不进版本库）。
import shutil as _sh

# 合成一个书签后，**背景只要一张**（按书签 id），但**每个角色仍各要一张
# 定位图**（按 <书签id>_<角色名> 命名）。
_BM_GFX = GAME / "gfx" / "interface" / "bookmarks"
_bm_targets = [(f"{BM_ID}.dds", "bm_1066_iberia.dds")] if _STARTS else []
for _ck in _STARTS:
    _bm_targets.append((
        f"{BM_ID}_bookmark_{CULTURE}_{_ck}.dds",
        "bm_1066_iberia_bookmark_iberia_1066_ramon_berenguer.dds"))
for _dst_name, _src_name in _bm_targets:
    _src = _BM_GFX / _src_name
    if not _src.is_file():
        # 定位图的原版命名不确定，退回通用占位
        _src = _BM_GFX / "_default.dds"
    # 书签立绘是**逐角色**资产，归该角色的模型 mod；底图归核心。
    _bm_ck = next((k for k in MP.CHARACTERS if _dst_name.endswith(f"_{k}.dds")), None)
    _bm_mod = O.models_of(_bm_ck) if _bm_ck else O.CORE
    _dst = O.path(_bm_mod, f"gfx/interface/bookmarks/{_dst_name}")
    _dst.parent.mkdir(parents=True, exist_ok=True)
    if _src.is_file():
        _sh.copyfile(_src, _dst)
        print(f"  书签贴图 {_dst_name}  <- {_src.name}")
    else:
        print(f"  !! 找不到书签贴图源: {_src}")

# 书签需要三个 loc key：名字、副标题、描述。缺了报 Missing loc。
# 三个角色合进一个书签后，**书签自己的两个 key 只要一份**（按 BM_ID），
# 角色的三个 key 仍按角色出。
BM_LOC = (f''' {BM_ID}: "Moe Portraits"
 {BM_ID}_desc: "Playable starts for the anime portrait project."
''' if _STARTS else '') + "".join(
    f''' bookmark_{CULTURE}_{_ck}: "{_cs["name_en"]}"
 bookmark_{CULTURE}_{_ck}_subheading: "$BOOKMARK_SUBHEADING_DEFAULT$"
 bookmark_{CULTURE}_{_ck}_desc: "{_cs["desc_en"]}"
''' for _ck, _cs in _STARTS.items())
BM_LOC_CN = (f''' {BM_ID}: "二次元肖像"
 {BM_ID}_desc: "二次元肖像项目的可玩开局。"
''' if _STARTS else '') + "".join(
    f''' bookmark_{CULTURE}_{_ck}: "{_cs["name_cn"]}"
 bookmark_{CULTURE}_{_ck}_subheading: "$BOOKMARK_SUBHEADING_DEFAULT$"
 bookmark_{CULTURE}_{_ck}_desc: "{_cs["desc_cn"]}"
''' for _ck, _cs in _STARTS.items())

# ================================================================ 本地化
# 本地化 key 不带 culture_ 前缀。引擎报的是
#   "Culture missing localization for moe_florence"
#   "Missing localization for moe_florence_collective_noun"
#   "Missing localization for moe_florence_prefix"
#   "Missing loc for name_list_moe_florence"
# 即直接用文化 id 本身作为 key。
# 王朝名的 loc key 就是 name_list 里写的字符串（dynn_ 前缀是命名习惯，
# 不是引擎语义）；人名的 loc key 是名字本身。
#
# 只补原版没有的名字。原版已有的（Adalbert、Conrad、Florence 等日常西欧名
# 都在 localization/*/names/character_names_l_english.yml 里）如果再声明一遍，
# 报 "Duplicate localization key. Key 'X' is defined in both ..."。
_VANILLA_LOC = GAME / "localization"


def _vanilla_name_keys():
    """扫原版 localization 的人名文件，收集已定义的 key。"""
    keys = set()
    for lang in ("english", "simp_chinese"):
        d = _VANILLA_LOC / lang / "names"
        if not d.is_dir():
            continue
        for f in d.glob("*.yml"):
            try:
                for line in f.read_text(encoding="utf-8-sig",
                                        errors="replace").splitlines():
                    s = line.strip()
                    if not s or s.startswith("#") or s.startswith("l_"):
                        continue
                    k = s.split(":", 1)[0].strip()
                    if k:
                        keys.add(k)
            except OSError:
                pass
    return keys


_HAVE = _vanilla_name_keys()
_NEW_NAMES = [n for n in MALE_NAMES + FEMALE_NAMES if n not in _HAVE]
# 槽位角色的名字（history 的 name = "Lenna" 会按 loc key 找；缺了刷 `Missing loc for name`，2026-09-06 实机）
_SLOT_NAME_CN = {c["name_en"]: c["name_cn"] for c in ACTIVE}
_NEW_NAMES += [c["name_en"] for c in ACTIVE if c["name_en"] not in _HAVE and c["name_en"] not in _NEW_NAMES]
print(f"  人名本地化: 原版已有 {len(_HAVE)} 个 key，"
      f"需补 {len(_NEW_NAMES)}/{len(MALE_NAMES) + len(FEMALE_NAMES)} 个")

_dyn_en = "\n".join(f' {d}:0 "{d[5:] if d.startswith("dynn_") else d}"'
                    for d in DYNASTY_NAMES)
_names_en = "\n".join(f' {n}:0 "{n}"' for n in _NEW_NAMES)

LOC = f'''l_english:
 {CULTURE}:0 "Moe"
 {CULTURE}_collective_noun:0 "Moe"
 {CULTURE}_prefix:0 "Moe "
 {HERITAGE}_name:0 "Moe"
 {HERITAGE}_collective_noun:0 "Moe"
 name_list_{CULTURE}:0 "Moe"
{_dyn_en}
{_names_en}
{BM_LOC}'''
w(f"localization/english/moe_{CULTURE}_l_english.yml", LOC)

# ---------------------------------------------------------------- 第 3/6 项文案
# 交互 + 换装弹窗 + 开局说明。**必须两种语言都写全**，缺了会显示成 key
# 本身（比如界面上直接出现 `moe_cosplay_interaction`），不报错。
_POSE_LOC_EN = "\n".join(
    f' {COS_EVT}.0002.{k}:0 "{en}"' for k, _s, _cn, en, _p in POSES) + "\n" + "\n".join(
    f' {GROUP}_cos_group_{_PC.group_key(ge)}:0 "{ge}"' for _gc, ge, _i in POSE_GROUPS)
_POSE_LOC_CN = "\n".join(
    f' {COS_EVT}.0002.{k}:0 "{cn}"' for k, _s, cn, _en, _p in POSES) + "\n" + "\n".join(
    f' {GROUP}_cos_group_{_PC.group_key(ge)}:0 "{gc}"' for gc, ge, _i in POSE_GROUPS)
_POSE_NAMES_EN = (f"{len(POSES)} poses grouped by " + ", ".join(e for _g, e, _i in POSE_GROUPS))

COS_LOC_EN = f'''l_english:
 {COS_INT}:0 "Change Cosplay Outfit"
 {COS_INT}_desc:0 "Open the wardrobe. Changing into collected outfits and changing poses are free."
 {COS_EVT}.0001.t:0 "Change Cosplay Outfit"
 {COS_EVT}.0001.desc:0 "A wardrobe of otherworldly garments awaits. Which shall it be?"
 {COS_EVT}.0001.model:0 "Change outfit (pick a new model)"
 {COS_EVT}.0001.pose:0 "Change idle pose"
 {COS_EVT}.0001.cancel:0 "On second thought, no"
 {COS_EVT}.0002.t:0 "Choose an Idle Pose"
 {COS_EVT}.0002.desc:0 "How should this character stand when at rest? {_POSE_NAMES_EN}."
{_POSE_LOC_EN}
 {GROUP}_cos_title:0 "Change Cosplay Outfit"
 {GROUP}_cos_models:0 "Models"
 {GROUP}_cos_page:0 "Page"
 {GROUP}_cos_pose_header:0 "Idle pose"
 {GROUP}_cos_close:0 "Close"
 {GROUP}_cos_cancel:0 "Cancel Cosplay"
 {GROUP}_cos_cancel_tt:0 "Stop using an anime model. The character becomes an ordinary random character of their culture and keeps the beauty trait. Cannot be undone, and the former appearance is not restored."
 {GROUP}_cos_skin_tt:0 "Cycle this character's other outfits"
 {GROUP}_cos_drag_tt:0 "Drag the title bar to move this window"
 {GROUP}_cos_drag_hint:0 "(drag to move)"
 {GROUP}_cos_lobby_open:0 "Change Cosplay Outfit"
 {GROUP}_cos_lobby_open_tt:0 "Preview any appearance before starting. Only the final choice becomes your starter outfit."
 {GROUP}_cos_ignore_gender:0 "Ignore Gender"
 {GROUP}_cos_ignore_gender_on:0 "Ignore Gender: #high On#!"
 {GROUP}_cos_ignore_gender_off:0 "Ignore Gender: Off"
 {GROUP}_cos_ignore_gender_tt:0 "Global switch. When on, anyone may be dressed by hand in models and skins of either sex. Random assignment always follows the character's sex. Turning it off keeps outfits already worn."
 {GS_EVT}.0002.t:0 "Moe Portraits"
 {GS_EVT}.0002.desc:0 "{CR.welcome_description('english', COS_COST, _POSE_NAMES_EN)}"
 {GS_EVT}.0002.ok:0 "Understood"
'''
w(f"localization/english/moe_{GROUP}_ui_l_english.yml", COS_LOC_EN)

COS_LOC_CN = f'''l_simp_chinese:
 {COS_INT}:0 "更换cos服"
 {COS_INT}_desc:0 "打开衣橱，已收藏的衣装与待机姿势均可免费切换。"
 {COS_EVT}.0001.t:0 "更换cos服"
 {COS_EVT}.0001.desc:0 "衣柜里挂着几套异世界的行头。换哪一套？"
 {COS_EVT}.0001.model:0 "换一套服装（重新抽取模型）"
 {COS_EVT}.0001.pose:0 "更换待机姿势"
 {COS_EVT}.0001.cancel:0 "算了，不换"
 {COS_EVT}.0002.t:0 "选择待机姿势"
 {COS_EVT}.0002.desc:0 "闲下来的时候摆什么姿势？可选：{_pose_list_cn}。"
{_POSE_LOC_CN}
 {GROUP}_cos_title:0 "更换cos服"
 {GROUP}_cos_models:0 "模型"
 {GROUP}_cos_page:0 "页"
 {GROUP}_cos_pose_header:0 "待机姿势"
 {GROUP}_cos_close:0 "关闭"
 {GROUP}_cos_cancel:0 "取消cos"
 {GROUP}_cos_cancel_tt:0 "不再使用二次元模型。角色变回所属文化的随机普通角色，美貌特质保留。无法撤销，也不会恢复原来的长相。"
 {GROUP}_cos_skin_tt:0 "轮换这位角色的其他皮肤"
 {GROUP}_cos_drag_tt:0 "按住标题栏拖动窗口"
 {GROUP}_cos_drag_hint:0 "（拖动标题栏可移动）"
 {GROUP}_cos_lobby_open:0 "更换cos服"
 {GROUP}_cos_lobby_open_tt:0 "开局前可预览所有外观，仅最终选择作为起始衣装入藏。"
 {GROUP}_cos_ignore_gender:0 "忽略性别"
 {GROUP}_cos_ignore_gender_on:0 "忽略性别：#high 开#!"
 {GROUP}_cos_ignore_gender_off:0 "忽略性别：关"
 {GROUP}_cos_ignore_gender_tt:0 "全局开关。打开后，手动换装可以选择另一性别的模型与皮肤；随机分配始终按角色性别。关闭开关不会脱下已穿的衣装。"
 {GS_EVT}.0002.t:0 "二次元立绘"
 {GS_EVT}.0002.desc:0 "{CR.welcome_description('simp_chinese', COS_COST, _pose_list_cn)}"
 {GS_EVT}.0002.ok:0 "明白了"
'''
w(f"localization/simp_chinese/moe_{GROUP}_ui_l_simp_chinese.yml", COS_LOC_CN)

# ---------------------------------------------------------------- 可选文化包的脚本与文本
# 两个自定义传统、cosplay 认同的加成、鼠族兵种定义与本地化、含传统要求的欢迎文本
# （localization/replace 覆盖同名 key）、从上面 CULTURE_TXT 派生的完整 moe 文化覆盖，
# 全部归 moe_cultural_units。只写这些路径：兵模 mesh/anim/DDS 由各自的生成器负责，
# 这里不 reset 该包目录。
import moe_cultural_units as CU
for _cu_rel, _cu_text in CU.files(GAME, CULTURE_TXT,
                                   {"english": _POSE_NAMES_EN, "simp_chinese": _pose_list_cn}).items():
    w(_cu_rel, _cu_text, mod=O.UNITS)
for _cu_rel, _cu_src in CU.binaries().items():
    O.copy(O.UNITS, _cu_rel, _cu_src)
# 旧版整份文化覆盖（99_ 前缀，与核心同 key 不同路径）已由同路径 01_moe.txt 取代。
_cu_legacy = O.root(O.UNITS) / CU.LEGACY_CULTURE_REL
if _cu_legacy.is_file():
    _cu_legacy.unlink()
    print(f"  删除 [{O.UNITS}] {CU.LEGACY_CULTURE_REL}（改为同路径覆盖核心文化）")

_dyn_cn = "\n".join(
    f' {d}:0 "{NAME_CN.get(d[5:] if d.startswith("dynn_") else d, d)}"'
    for d in DYNASTY_NAMES)
_names_cn = "\n".join(f' {n}:0 "{NAME_CN.get(n, _SLOT_NAME_CN.get(n, n))}"' for n in _NEW_NAMES)

LOC_CN = f'''l_simp_chinese:
 {CULTURE}:0 "二次元"
 {CULTURE}_collective_noun:0 "二次元人"
 {CULTURE}_prefix:0 "二次元"
 {HERITAGE}_name:0 "二次元"
 {HERITAGE}_collective_noun:0 "二次元人"
 name_list_{CULTURE}:0 "二次元"
{_dyn_cn}
{_names_cn}
{BM_LOC_CN}'''
w(f"localization/simp_chinese/moe_{CULTURE}_l_simp_chinese.yml", LOC_CN)

# 外层 `.mod` 不在这里写了（2026-08-29）。单体 mod 已退役，7 个子 mod 的
# descriptor 与 `.mod` 由 `tools/build/finalize_mods.py` 按 `INFO` 统一生成 ——
# 这里再写一份 `moe_florence_poc.mod` 只会在每次构建后把已清理掉的老 mod
# 文件重新变出来（我移进 trash 后就被这一步拉回来过）。

# VERIFY.md 已删（2026-08-26）：整份在验证「独立 portrait type group
# 能否被选中」，那个命题早已实机确认为可以，三个角色都跑通了。文档里
# 提到的 01_moe_types.txt / optional_human_override/ 都不存在了，骨数
# 167 也早变成 189(芙洛伦)/473(洛茜)，而它的第 2 步还教人删原版
# 00_human_types.txt。**只删文件不够**，生成逻辑一起删才不会被重建写回。


# ================================================================ 贴图转 DDS
# CK3 资源管线只认 .dds。PNG 即使放对目录、.asset 里引用正确，
# 引擎也直接报 "Failed to find texture 'x.png'"，连尝试加载都不做。
# 必须在 .asset 写完之后转 —— 转换脚本会同步改写里面的贴图引用。
# 转换**之前**先审 alpha 语义。必须在这之前 —— 转换会 unlink 源 PNG，
# 之后只剩已二值化的 DDS，就再也看不出源图原本有没有中间 alpha。
# 2026-09-11 加：此前判据只挂在贴图文件名的关键词表上，而语义在 shader 槽位上。
# 文件名是各家美术随手起的（gfl2 的表情贴片图集就叫 `extra.png`），关键词表
# 永远追不齐 —— 于是它从没被二值化，糊脸的假阴影在 gfl2 侧一直存在，
# 而构建 rc=0、日志里几百行转换记录看着完全正常。
print("")
print("按 diffuse shader 槽拆分共享 alpha:")
_split_alpha = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "textures"
                         / "prepare_opacity_alpha_variants.py"),
     *[str(MP.base_of(_g)) for _g in O.GAMES if MP.base_of(_g).is_dir()]],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
_enc0 = sys.stdout.encoding or "utf-8"
for _line in ((_split_alpha.stdout or "") + (_split_alpha.stderr or "")).splitlines():
    if _line.strip():
        print("  " + _line.encode(_enc0, "replace").decode(_enc0, "replace"))
if _split_alpha.returncode != 0:
    sys.exit("**共享贴图 alpha 拆分失败** —— 不能让透明度和染色遮罩共用同一 alpha。")

print("贴图 alpha 语义自检:")
_alpha = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "verify"
                         / "verify_texture_alpha_contract.py")],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
_enc0 = sys.stdout.encoding or "utf-8"
for _line in ((_alpha.stdout or "") + (_alpha.stderr or "")).splitlines():
    if _line.strip():
        print("  " + _line.encode(_enc0, "replace").decode(_enc0, "replace"))
if _alpha.returncode != 0:
    sys.exit("**贴图 alpha 语义自检失败** —— 上面列出的 diffuse 挂在"
             "「A 通道是透明度」的 shader 槽上却带中间 alpha，且合同没认领它们。"
             "中间 alpha 会被 alpha-to-coverage 渲成点阵（透出背景，或糊上颜色）。"
             "把部件名补进 moe_material_contract 的关键词表再构建。")

print("")
print("贴图转 DDS:")
_conv = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "textures" / "convert_textures_dds.py"),
     *[str(MP.base_of(_g)) for _g in O.GAMES if MP.base_of(_g).is_dir()]],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
# Windows 控制台是 GBK，子进程输出里的非 GBK 字符会让 print 抛
# UnicodeEncodeError。转发前按控制台编码过滤掉不可表示的字符。
_enc = sys.stdout.encoding or "utf-8"
for _line in (_conv.stdout or "").splitlines():
    if _line.strip():
        print("  " + _line.encode(_enc, "replace").decode(_enc, "replace"))
if _conv.returncode != 0:
    # **必须中断**：原来只 print，构建照样走完、rc=0。贴图没转成 DDS 意味着
    # 引擎报 Failed to find texture、那个角色整体不渲染 —— 这类失败不能静默。
    print(f"  !! 转换失败 (exit {_conv.returncode})")
    if _conv.stderr:
        print(f"  {_conv.stderr[:800]}")
    sys.exit(f"**贴图转 DDS 失败 (exit {_conv.returncode})** —— "
             f"PNG 留在包里而 .asset 已改引用 .dds，引擎会报"
             f" Failed to find texture，角色整体不渲染。")


# ---------------------------------------------------------------- 放大立绘 gui
# 必须在安装之前跑：本脚本开头 rmtree 了整个 mod 目录，gui 文件只能由脚本生成。
# 从**原版**打补丁，不是复制 Big Portraits 的文件（那份缝了别家 mod 的挂钩，
# 会刷 206 条 marry_ring.png not found）。详见该脚本头部说明。
print("")
print("生成放大立绘 gui（从原版打补丁）:")
_bp = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "build" / "build_big_portrait_gui.py")],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
for _line in (_bp.stdout or "").splitlines():
    if _line.strip():
        print("  " + _line.encode(_enc, "replace").decode(_enc, "replace"))
if _bp.returncode != 0:
    # 补丁命中数不符会在这里失败 —— 那说明原版版式变了，必须人工核对，
    # 不能让它静默跳过（gui 缺文件的表现是立绘尺寸悄悄回到原版大小）。
    print(f"  !! 生成失败 (exit {_bp.returncode})")
    if _bp.stderr:
        print(f"  {_bp.stderr[:1200]}")

# ================================================================ 安装
# 必须在所有文件写完之后再拷贝，否则已安装副本会缺文件
#
# **装到隔离 userdir，不是常规文档 mod 目录**（2026-08-23 用户决定）。
# 常规那份已删除；这里若还按 CK3_MOD_DIR 装就会把 920MB 又装回去，
# 撤销那个决定。
# 注意不能只判 CK3_MOD_DIR.is_dir() —— 那个 mod 目录本身仍存在
# （装着用户订阅的其它 mod），存在性判据在这里没有意义。
# **不再往 userdir 装单体 mod**（2026-08-28）。现在进游戏的是
# 7 个 mod 由各生成器**直写** mod/（见 moe_out.py），
# 再由 `tools/build/finalize_mods.py` 补 descriptor、自检、安装。
#
# 这一步留着的害处是实打实的：它每次构建都把 1.5 GB 的单体副本写回
# `work/ck3_florence_userdir/mod/moe_florence_poc/`，而那份与拆分后的 mod
# **提供同名文件**，两者都在 userdir 里时覆盖关系不可控。用户 2026-08-28 让我
# 把它清掉，我移进 trash 之后下一次构建又被这一步拉了回来。
#
# 2026-08-29：单体目录本身也退役了（连同一次性的搬迁脚本一起进 trash）。
# 2491 个 .anim / 8 个 mesh / 109 张贴图已逐个核对存在于新位置后才移走。
print("\n产物直写 mod/（各子 mod）。装进游戏请跑："
      "\n  python tools/build/finalize_mods.py"
      "\n（或一条 python tools/build_all.py 走完全流程）")

# ================================================================ 清单
manifest = {
    "mod_name": MOD_NAME,
    "culture": CULTURE,
    "portrait_type": "moe",
    "entities": {"head": ENTITY_HEAD, "torso": ENTITY_TORSO},
    "meshes": {"head": MESH_HEAD, "torso": MESH_TORSO},
    "animation_set": ANIM_SET,
    "bone_count": len(bones),
    "semantic_contract": report["semantic_contract"],
    "attach": {"parent_joint": "bn_m_spine_up", "child_joint": "bn_m_root"},
    "textures_copied": tex_copied,
    "textures_missing": sorted(tex_needed - set(tex_copied)),
    "parts": parts,
    "files": sorted(f"{m}/{q.relative_to(O.root(m)).as_posix()}"
                    for m in O.all_mods() if O.root(m).is_dir()
                    for q in O.root(m).rglob("*") if q.is_file()),
}
(POC / "mod_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n清单: {POC / 'mod_manifest.json'}")
print("\n[MOD BUILD DONE]")
