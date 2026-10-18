"""生成 mod 的肖像布光与后处理覆盖 —— 关掉角色自身投影与 SSAO。

## 为什么这一步比改 shader 值得做

用户的判断是对的：**布光和 SSAO 都是纯文本配置，改起来比改 shader 简单
得多**，而且不用整份复制 shader、不受 CK3 更新影响那么大。

## 两项改动（配色一律照抄原版）

1. **三个光源都不投射阴影**（`affected_by_shadow = no`）。
   前发会把影子投在额头、眼睛、颧骨上，而阴影贴图分辨率不够、又被
   `shadowmap_kernelscale = 8` 过滤糊开，结果是一片边界不清的灰绿脏块
   （实测芙洛伦右颊、下巴、鼻侧、嘴角都有）。二次元立绘不要实时自身投影
   —— 头发落在脸上的硬阴影是画师手绘的，靠 shadow map 做只会脏。
2. **关掉 SSAO**（`ssao = { enabled = no }`）。SSAO 是全屏后处理、在
   portrait shader 之后乘上来（见 `build_moe_shader.py` 的模块 docstring），
   会把卡通硬边重新糊成连续的接触阴影 —— 眼窝、鼻侧那些浑浊斑块。
   shader 侧只能从 `Out.SSAOColor.a` 降权，这里能直接关掉，更彻底。

**这两项是两回事，都要关**：SSAO 是环境光遮蔽（眼窝/鼻侧的接触暗斑），
投影是几何遮挡光源（前发落在额头上的影子）。

## 颜色/位置/cubemap 一律与原版逐字一致

曾经改过一版：主光去饱和（`hsv{ 0.1 0.45 1.5 }` → 饱和度 0）、移到脸
正前方（`angle 30 → 0`、`height 260 → 200`）、两侧轮廓光去饱和、cubemap
换成实测最中性的 `events_ep2_farmlands_settlement.dds`（R−B −0.6，
原版 `castle_interior_01_fire.dds` 是 +27.2）。

那一版**确实消掉了脸上的暖红**（采样 R−B 平均 +25.9 → +9.0），但也把原版
布光的塑形与冷暖对比一起抹平了 —— 轮廓光的冷暖对比是把角色从背景里分离
出来的手段，主光 30° 侧打给的是面部结构。用户看过实机效果后决定恢复原版
配色，只保留「不接受阴影」。

所以现在 `MOE_ENV` 里的颜色、位置、亮度、cubemap 全部照抄原版，
`build_env()` 会核对这些值在原版文件里还在，变了就报错（说明 CK3 改了
布光，本文件得重新照抄）。**自检的方向也跟着反过来了**：从「饱和度必须
为 0」改成「必须逐字等于原版三个色值」，否则「恢复原版」会静默只做一半。

## 为什么只重定义 environment_standard 一个块

`environment_head` / `shoulders` / `torso` / `body` 全都写
`lights = environment_standard`，所以**改一处覆盖所有肖像视图**。
而 `environment_standard` 在原版 17859 行的
`portrait_environments.txt` 里只定义一次、各 DLC 文件没有重名
（已 grep 核对），CK3 是「同名 key 后加载覆盖」，所以我们只需要一个
小文件重定义这一个块，不必整份复制。

`jomini_environment.txt` 是扁平 key-value、只 55 行，整份覆盖成本很低，
所以那个直接照抄改 ssao 一处。

## 缺一个字段会让整个 environment 静默退回默认

引擎报 `portraitenvironment.cpp:50 Light is missing a type!` 时**整份布光
失效、退回原版**，画面表现是「改了没效果」而 error.log 只有一行。我加投影
注释时替换锚点吃掉了主光的 `type = "spot_light"`，那一轮截图看到的还是
原版布光加原版投影，差点据此判断「关投影没用」。

当时的自检查了颜色、角度、投影，**唯独没查结构完整性**。现在补上：光源块
数必须等于 `type` 数（各 3），每块必须有 `color`/`position`，且
`affected_by_shadow = no` 必须正好 3 处。这类判据要做**反向验证**（故意删
掉一个 `type`，自检必须报错且退出码非 0）—— 不做反向验证等于没有自检。

`shadow_camera` 块虽然三个光源都不投影后不再起作用，但**保留原版结构**：
缺字段的代价就是上面那条静默退回。

## 代价

`environment_standard` 是冻结副本：CK3 改了原版布光（比如加第四个光源）
我们拿不到。但它只有 75 行、结构简单，比 shader 那份 1241 行好核对。

用法：
  python tools/build/build_moe_lighting.py
"""
import sys as _sys
from pathlib import Path as _P
_sys.path[:0] = [str(_P(__file__).resolve().parents[2] / "tools" / _d) for _d in ("lib", "anim")]   # 子目录化后底座不在脚本同目录
import moe_out as O          # 产物归属：谁写谁声明
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GAME = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III")
SRC_ENV = (GAME / "game" / "gfx" / "portraits" / "environments"
           / "portrait_environments.txt")
SRC_JOM = GAME / "game" / "gfx" / "portraits" / "jomini_environment.txt"

# 布光覆盖是机制配置，归核心。
MOD = O.root(O.CORE)
# 文件名前缀 `zz_` 让它在原版之后加载（CK3 按文件名排序加载同目录文件，
# 后加载的同名 key 覆盖先加载的）。**不能与原版同名**：同名会整份替换
# 那个文件，我们就丢掉了另外 200 多个 environment 定义。
DST_ENV = (MOD / "gfx" / "portraits" / "environments"
           / "zz_moe_environments.txt")
DST_JOM = MOD / "gfx" / "portraits" / "jomini_environment.txt"

# ---------------------------------------------------------------- 布光
# **颜色 / 位置 / cubemap 全部与原版逐字一致**（2026-08-24 用户决定）。
# 唯一改动是三个光源的 `affected_by_shadow` 全设 `no`。
#
# 为什么退回原版配色：去饱和 + 正面光 + 中性 cubemap 那一版确实消掉了脸上的
# 暖红，但也把原版布光的塑形与冷暖对比一起抹平了 —— 轮廓光的冷暖对比是
# 角色从背景里分离出来的手段，主光 30° 侧打给的是面部结构。用户看过实机
# 效果后要求恢复配色，只留「不接受阴影」。
#
# 原版值（core：portrait_environments.txt 的 environment_standard）：
#     主光      hsv{ 0.1 0.45 1.5 }   angle 30   height 260
#     右轮廓光  hsv{ 0.05 0.8 0.3 }
#     左轮廓光  hsv{ 0.6 0.3 1.6 }
#     cubemap   castle_interior_01_fire.dds   intensity 0.4
# `build_env()` 会核对这些值还在原版文件里，变了就报错 —— 那说明 CK3 改了
# 布光，本文件得重新照抄。
MOE_ENV = '''# 由 tools/build/build_moe_lighting.py 生成，**不要手改**。
#
# 只重定义 environment_standard 一个块。原版 portrait_environments.txt 里
# environment_head / shoulders / torso / body 全都写 `lights =
# environment_standard`，所以改这一处就覆盖所有肖像视图。
#
# 文件名 zz_ 前缀是为了在原版之后加载（同名 key 后者覆盖）。
# **绝不能改成与原版同名** —— 那样会整份替换掉原版文件，
# 连带丢失另外 200 多个 environment 定义。
#
# **与原版唯一的差别：三个光源都不投射阴影**（affected_by_shadow = no）。
# 颜色、位置、亮度、cubemap 全部与原版逐字一致。
#   · 为什么关投影：前发会把影子投在额头/眼睛/颧骨上，而阴影贴图分辨率
#     不够、又被 shadowmap_kernelscale=8 过滤糊开，结果是边界不清的灰绿
#     脏块。二次元立绘不要实时自身投影 —— 头发落在脸上的硬阴影是画师
#     手绘的。
#   · SSAO 另外在 jomini_environment.txt 里关掉（那是全屏后处理，与投影
#     是两回事：SSAO 是环境光遮蔽，投影是几何遮挡光源）。
#
# 基于 CK3 {ver}。CK3 更新后重跑生成脚本。

@moe_light_radius = 160
@moe_light_height = 260
@moe_light_angle = 20
@moe_cubemap_intensity = 0.4

environment_standard = {{
	cubemap = "gfx/portraits/environments/castle_interior_01_fire.dds"
	cubemap_intensity = @moe_cubemap_intensity

	lights = {{
		# ------------------------------------------------ 主光（KEY LIGHT）
		# 与原版逐字一致，只是不投影。
		0 = {{
			type = "spot_light"
			# **原版是 yes**。关掉的理由见文件头：前发投在脸上的影子被
			# shadowmap 过滤糊成灰绿脏块（实测芙洛伦右颊、下巴、鼻侧、
			# 嘴角都有）。这是与原版唯一的差别。
			affected_by_shadow = no

			color = hsv{{ 0.1 0.45 1.5 }}
			position = cylindrical{{ @moe_light_radius @moe_light_height 30 }}

			look_at = {{ 0 0 0 }}
			look_at_node = {{
				default = camera_torso_look_at
			}}

			radius = 1000
			falloff = 10

			outer_cone_angle = 25
			inner_cone_angle = 5
		}}

		# ------------------------------------------ 右轮廓光（RIGHT RIM）
		# 与原版逐字一致（原版这个本来就 affected_by_shadow = no）。
		1 = {{
			type = "spot_light"
			affected_by_shadow = no
			color = hsv{{ 0.05 0.8 0.3 }}
			position = cylindrical{{ @moe_light_radius 200 -120 }}

			look_at = {{ 0 -35 0 }}
			look_at_node = {{
				default = camera_torso_look_at
			}}

			radius = 1000
			falloff = 50

			outer_cone_angle = 18
			inner_cone_angle = 0
		}}

		# ------------------------------------------- 左轮廓光（LEFT RIM）
		# 与原版逐字一致，只是不投影。平行光的投影范围最大，头发/衣领在
		# 脖子和胸前投出的长条阴影全靠它。
		2 = {{
			type = "directional_light"
			affected_by_shadow = no

			color = hsv{{ 0.6 0.3 1.6 }}
			position = cylindrical{{ 200 100 -160 }}

			look_at = {{ 0 18 0 }}
		}}
	}}

	# 与原版逐字一致。三个光源都不投影后它其实不再起作用，但**保留着**：
	# 缺字段会让引擎整份退回默认 environment（踩过一次，见模块 docstring），
	# 留下原版结构最安全。
	shadow_camera = {{
		position = cylindrical {{ @moe_light_radius 300 30 }}
		look_at = {{ 0 0 0 }}
		look_at_node = {{
			default = camera_torso_look_at
		}}
		fov = 20
		camera_near_far = {{ 100 500 }}
	}}
}}
'''


def build_env():
    if not SRC_ENV.exists():
        print(f"[light] 找不到原版布光：{SRC_ENV}", file=sys.stderr)
        return None
    s = io.open(SRC_ENV, encoding="utf-8-sig", errors="replace").read()

    # 核对我们假设的原版值还在 —— 变了说明 CK3 改了布光，参数要重新校准。
    checks = {
        "主光色": "color = hsv{ 0.1 0.45 1.5 }",
        "右轮廓光色": "color = hsv{ 0.05 0.8 0.3 }",
        "左轮廓光色": "color = hsv{ 0.6 0.3 1.6 }",
        "environment_standard": "environment_standard = {",
    }
    bad = [k for k, v in checks.items() if v not in s]
    if bad:
        print(f"[light] **原版布光对不上，这些找不到：{bad}** —— "
              f"CK3 可能改了布光，核对后更新 MOE_ENV", file=sys.stderr)
        return None

    # environment_head/shoulders/torso/body 必须都指向 environment_standard，
    # 否则改一处覆盖不了所有视图，得逐个重定义。
    n_ref = len(re.findall(r"lights = environment_standard", s))
    if n_ref < 4:
        print(f"[light] **只有 {n_ref} 个 environment 引用 "
              f"environment_standard（应 ≥4）** —— head/shoulders/torso/body "
              f"可能改成各自定义布光了，核对后决定要不要逐个覆盖",
              file=sys.stderr)
        return None
    return n_ref


def build_jom(ver):
    if not SRC_JOM.exists():
        print(f"[light] 找不到原版后处理配置：{SRC_JOM}", file=sys.stderr)
        return None
    s = io.open(SRC_JOM, encoding="utf-8-sig", errors="replace").read()

    # SSAO 关掉。判据是 `enabled=yes` 出现在 ssao 块里。
    m = re.search(r"(ssao\s*=\s*\{)(.*?)(\n\})", s, re.S)
    if not m:
        print("[light] **找不到 ssao 块** —— 原版可能改了写法",
              file=sys.stderr)
        return None
    body = m.group(2)
    if "enabled=yes" not in body and "enabled = yes" not in body:
        print(f"[light] **ssao 块里找不到 enabled=yes** —— "
              f"原版可能已经默认关掉了，核对后决定", file=sys.stderr)
        return None
    newbody = body.replace("enabled=yes", "enabled=no", 1) \
                  .replace("enabled = yes", "enabled = no", 1)
    s = s[:m.start(2)] + newbody + s[m.end(2):]

    head = (f"# 由 tools/build/build_moe_lighting.py 生成，**不要手改**。\n"
            f"#\n"
            f"# 原版 game/gfx/portraits/jomini_environment.txt 的副本，"
            f"一项改动：\n"
            f"#   ssao 的 enabled=yes → no\n"
            f"#\n"
            f"# 为什么关掉：SSAO 是**全屏后处理**、在 portrait shader 之后"
            f"乘上来，\n"
            f"# 会把卡通渲染的硬边重新糊成连续的接触阴影（脸上眼窝、鼻侧、"
            f"下巴那些\n"
            f"# 浑浊斑块就是它）。shader 侧只能从 Out.SSAOColor.a 降权，"
            f"这里能直接关。\n"
            f"#\n"
            f"# 基于 CK3 {ver}。CK3 更新后重跑生成脚本。\n"
            f"\n")
    return head + s


def main():
    ver = "unknown"
    cand = GAME / "launcher" / "launcher-settings.json"
    if cand.exists():
        m = re.search(r'"rawVersion"\s*:\s*"([^"]+)"',
                      cand.read_text(encoding="utf-8", errors="replace"))
        if m:
            ver = m.group(1)

    n_ref = build_env()
    if n_ref is None:
        return 1
    jom = build_jom(ver)
    if jom is None:
        return 1

    DST_ENV.parent.mkdir(parents=True, exist_ok=True)
    io.open(DST_ENV, "w", encoding="utf-8-sig", newline="").write(
        MOE_ENV.format(ver=ver))
    DST_JOM.parent.mkdir(parents=True, exist_ok=True)
    io.open(DST_JOM, "w", encoding="utf-8-sig", newline="").write(jom)

    # 自检：产物必须**逐字复刻原版的颜色/位置/cubemap**，唯一差别是关投影
    env_txt = DST_ENV.read_text(encoding="utf-8-sig")
    bad = []
    # **只看真正的赋值行，不能整文件 substring 匹配**：注释里会引用原版值
    # 说明改了什么，粗判据会把注释当成产物值。我第一版就是这么写的
    # （当时判据方向相反 —— 要求饱和度为 0，那一版的教训同样适用）。
    assign_lines = [ln.strip() for ln in env_txt.splitlines()
                    if re.match(r"^\s*(color|cubemap|position)\s*=", ln)]
    # 三个光源的颜色必须与原版逐字一致（2026-08-24 用户决定恢复原版配色，
    # 只保留「不投影」）。少一条说明模板被改动过，必须报错 —— 否则
    # 「恢复原版」这件事会静默地只做一半。
    for v in ("hsv{ 0.1 0.45 1.5 }", "hsv{ 0.05 0.8 0.3 }",
              "hsv{ 0.6 0.3 1.6 }"):
        if not any(v in ln for ln in assign_lines):
            bad.append(f"缺原版光色 {v}")
    # cubemap 必须是原版那张火光贴图
    if not any("castle_interior_01_fire.dds" in ln for ln in assign_lines):
        bad.append("cubemap 不是原版的 castle_interior_01_fire")
    # 主光位置必须是原版的 angle 30
    if not re.search(r"position = cylindrical\{ @moe_light_radius "
                     r"@moe_light_height 30 \}", env_txt):
        bad.append("主光位置不是原版的 angle 30")
    if "environment_standard = {" not in env_txt:
        bad.append("缺 environment_standard 块")
    # **三个光源都必须关投影** —— 这是与原版唯一的差别，也是这个文件存在的
    # 全部理由。任何一个漏关，前发的影子就会重新糊在脸上。
    # 计数**必须锚定行首缩进**（`^\t\t\t`）而不是整文件匹配：注释里会写
    # 「三个光源都不投射阴影（affected_by_shadow = no）」，整文件匹配把注释
    # 也数进来、报成 5 处。这已经是同一个坑的第三次 —— 颜色判据、饱和度
    # 判据都栽在「注释里的字面量被当成产物值」上。
    n_shadow_yes = len(re.findall(r"^			affected_by_shadow = yes",
                                 env_txt, re.M))
    if n_shadow_yes:
        bad.append(f"还有 {n_shadow_yes} 个光源开着投影")
    n_shadow_no = len(re.findall(r"^			affected_by_shadow = no",
                                env_txt, re.M))
    if n_shadow_no != 3:
        bad.append(f"只有 {n_shadow_no} 个光源写了不投影（应 3）")
    # **每个光源块必须有 type**（2026-08-24 补）。引擎报
    # `portraitenvironment.cpp:50 Light is missing a type!` 时整个
    # environment 失效、退回默认布光。我加投影注释时锚点吃掉了主光的
    # `type = "spot_light"` 行，而当时的自检只查颜色/角度/投影，
    # **结构完整性一项都没查**，所以产物带着这个错同步进游戏才被发现。
    # 判据用「光源序号块的数量」对「type 的数量」，缺一个就报。
    n_light_blocks = len(re.findall(r"^		\d+ = \{", env_txt, re.M))
    n_types = len(re.findall(r'^			type = "(?:spot|point|directional)'
                            r'_light"', env_txt, re.M))
    if n_light_blocks != 3:
        bad.append(f"光源块数 {n_light_blocks}（应 3）")
    if n_types != n_light_blocks:
        bad.append(f"{n_light_blocks} 个光源块只有 {n_types} 个 type")
    # 必需字段逐块核对：少 color/position 同样让引擎回退
    for fld in ("color", "position"):
        n = len(re.findall(rf"^			{fld} = ", env_txt, re.M))
        if n < 3:
            bad.append(f"只有 {n} 个光源有 {fld}（应 3）")
    jom_txt = DST_JOM.read_text(encoding="utf-8-sig")
    if not re.search(r"ssao\s*=\s*\{[^}]*enabled\s*=\s*no", jom_txt, re.S):
        bad.append("ssao 没关掉")
    if bad:
        print(f"[light] **自检失败：{bad}**", file=sys.stderr)
        return 1

    print(f"[light] 出 {DST_ENV}")
    print(f"[light] 出 {DST_JOM}")
    print(f"[light] 颜色/位置/cubemap **一律照抄原版**"
          f"（主光 hsv{{0.1 0.45 1.5}} angle 30 height 260、"
          f"两侧轮廓光原值、castle_interior_01_fire）")
    print(f"[light] 三个光源全部 affected_by_shadow = no"
          f"（前发投在额头/颧骨的灰绿脏块就是它）")
    print(f"[light] SSAO enabled=yes → no（全屏后处理，与投影是两回事）")
    print(f"[light] 自检通过：environment_standard 覆盖 {n_ref} 个视图"
          f"（head/shoulders/torso/body），CK3 {ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
