# PIPELINE：从 PMX 到 CK3 里能用的肖像

本文按脚本里实际的调用顺序写，不照抄过期文档。每一步给：输入、输出、脚本（相对 `tools/`）、示例命令、验收判据、常见失败。命令都在工程根目录下执行；本包只带 `tools/`，工程根里的 `work/`、`mod/`、`assets/` 需要自己有。

先读 `README.md` 的“当前状态”：2026-10-07 起不再用生成器覆盖 `mod/`。下面的 S6、S8 里凡是会写 `mod/` 的步骤，都只能在沙盒里跑（`MOE_OUT_ROOT=<沙盒目录>`）。

## 约定

- 角色有三个名字：短键 `ck`（如 `lau`）、`slug`（如 `endfield_laurentina`，只用于仓库侧目录）、mod 内前缀 `moe_<ck>_`。三者都登记在 `tools/lib/moe_chars.py` 的 `CHARS`，**加角色只改这一处**，其余表全部派生自它。
- `moe_chars.ready(ck)` 的判据是烘焙目录里有 `moe_torso.mesh`；`moe_chars.active()` 是就绪角色，`build_all.py` 自动纳入。
- 每个脚本开头把 `tools/lib` 与 `tools/anim` 放进 `sys.path`，根目录由 `Path(__file__).resolve().parents[2]` 推出。
- Blender 5.2 后台脚本一律 `blender.exe -b ... --python <脚本> -- <参数>`，`--` 之后才是脚本参数。下文把它写成 `<BLENDER>`。

## 硬顺序（违反了几乎都是静默失败）

1. `build_moe_mod.py` 开头清空整个 mod 目录。它之后的 shader、gui、描边、布光、道具、换装 gui、register 每一步都得补跑，漏哪步就静默丢哪项功能。
2. `register_all_anims.py` 必须在 `build_moe_mod.py` 之后：后者整体重写 `.asset`，已注册的 state 会被冲掉，而进游戏闸会因为“只剩 idle”假通过。
3. 描边 shader 必须在放大立绘 gui 之后：gui 引用它产出的文件。
4. head 动画要在 torso 动画之后生成：嫁接从已生成的 torso 产物取脊椎。torso 重定向后，head 必须重新嫁接。
5. 重导 mesh 后骨数必须与所有 `.anim` 严格相等，否则 entity 整体不渲染且本地渲图看不出。要重跑 torso 与 head 两步。
6. 构建失败必须中断装机链。不要把“构建 + 装机 + 启动”写成一行；`build_all.py` 任一步非零就退出，别绕过。
7. 物理解算读 `work/base_animation_inputs/<ck>/anims/` 的基础动画，不能读已含物理的模型包动画；烘焙期间不得运行任何会重写模型包 `.asset` 的构建，否则计划变空却返回 0。
8. 已发布角色的 `X.bake_dir(ck)` 指向不可变的发布目录，不带 `--out-dir` 重生成会写穿它。
9. 新包（载体架构）：`carrier_pipeline` 之后先 `replay_pack_fixes`，再 `vnode_fix_all`，顺序反了断言失败。这两个脚本在工程的 `work/` 下，未收进本包。
10. `finalize_mods.py` 默认先校验来源，再把物理优先的最终动画物化进模型包；`--no-physics` 是“从当前基础烘焙输入恢复无物理对照”，不是禁用某个独立包。是否传它由用户决定，不要自己加。较早的记录里“启用数 5 或 7”指的是物理包独立加载的旧状态，物理动画现在随模型包发布，那组数字不再适用。

## 总览

| 步 | 做什么 | 主要脚本 |
|---|---|---|
| S0 | 环境与自审 | `audit_repo.py` |
| S1 | 盘点 PMX 骨架 | `model/inventory_gfl2_batch.py` `model/compare_pmx_skeletons.py` |
| S2 | 建 head/torso 网格 | `model/build_florence_poc.py` |
| S3 | 烘表情 | `model/bake_moe_expressions.py` |
| S4 | 动画：重定向、嫁接、注册 | `anim/build_all_anims.py` `anim/graft_all_head_anims.py` `anim/register_all_anims.py` |
| S5 | 贴图 | `textures/build_char_textures.py` `textures/convert_textures_dds.py` |
| S6 | 生成 mod 文本并部署 | `build_all.py` `build/build_moe_mod.py` |
| S7 | 物理烘焙 | `physics/moe_bullet_bake.py` |
| S8 | 1.20 载体架构转换（新包） | `build/carrier_pipeline.py` |
| S9 | 装机与实机验收 | `build/finalize_mods.py` `build/launch_ck3.ps1` `verify/final_verify.py` |
| S10 | 发布 | `build/publish_mods.py` |

S1 到 S4 的批量入口是 `build/import_chars.py`：mesh、expr、torso、head 四步，产物存在就跳过，不含物理。

---

## S0 环境与自审

- 输入：工程根目录，Python 3.13，Blender 5.2 + mmd_tools + 打过容差补丁的 io_pdx_mesh，CK3 安装目录（读原版文件）。
- 脚本：`audit_repo.py`。
- 命令：`python tools/audit_repo.py`
- 验收：末尾两节“文档里的失效路径”“import 了没放进 sys.path 的子目录”应为 0。它按 ast 遍历所有 import 节点，函数内的懒导入也查。
- 常见失败：重组目录后 `python -c import` 冒烟全过，跑到某个角色才炸，原因是懒导入；用本脚本而不是冒烟。

## S1 盘点 PMX 骨架

- 输入：`assets/` 下的 PMX。
- 输出：`work/pmx_skeleton_inventory/<slug>.json`（骨数、头发/裙子链结构、`source_file`）。
- 脚本：`model/inventory_gfl2_batch.py`（批量，每个 PMX 一个独立 Blender 进程）、`model/inventory_pmx_skeletons.py`（单个，靠环境变量 `MOE_PMX_PATH`、`MOE_PMX_REPORT`）、`model/compare_pmx_skeletons.py`（跨模型比对）。
- 命令：
  ```
  python tools/model/inventory_gfl2_batch.py            # 只盘没盘过的
  python tools/model/compare_pmx_skeletons.py           # 会写 work/pmx_skeleton_inventory/comparison.*
  ```
- 验收：`source_file` 指向实际导入成功的 PMX；导入参数必须是 `rename_bones=False`、`fix_bone_order=False`，否则拿到的不是原始骨架。
- 常见失败：`import_chars.py` 报“缺盘点报告”，就是这一步没跑。`moe_chars.py` 导入时对每个候选角色读这些 JSON，缺任何一个就抛 `FileNotFoundError`。

## S2 建 head/torso 网格

- 输入：盘点 JSON、PMX、`lib/moe_material_contract.py` 里每模型的材质归属例外。
- 输出：`work/<slug>/moe_head.mesh`、`moe_torso.mesh`、`<slug>_report.json`、`work/blenderProj/<slug>.blend`。
- 脚本：`model/build_florence_poc.py`（新角色的起点）。
- 命令：`<BLENDER> -b --python tools/model/build_florence_poc.py -- --model <slug>`
- 验收：
  - 日志里 STEP 2 / 2b / 2c 三道归属校验都要看，每道都会漏。
  - `[归属判定] head=N body=M 判不出=K` 的 K 必须接近 0；全是“判不出”说明骨名空间不匹配，校验会假阴性。
  - `python tools/verify/check_meshsettings_coverage.py`：每个 submesh 都有 meshsettings。
  - `python tools/verify/check_ownership.py`：head/torso 顶点划分。
- 常见失败：
  - 材质名会骗人，按顶点主导骨的父链判归属；头里混进躯干骨驱动的顶点，袜环/飘带会脱节且日志零报错。
  - “身体没了”先查 clothes：torso 可能只有一千多个面，主体在衣服材质里。
  - `.mesh` 内部对象名必须与 `.asset` 的 `pdxmesh name` 一致；`build_moe_mod.py` 拷 mesh 时自动改名（用的是 `model/rename_mesh_object.py`）。

## S3 烘表情

- 输入：`work/blenderProj/<slug>.blend`（芙洛伦的是 `florence_poc.blend`）。
- 输出：`work/<ck>_anim_bake/` 下的 head/torso mesh（追加了 fx 代理骨）、表情 `.anim`、`bake_report.json`；`work/blenderProj/<ck>_expression_rig.blend`。
- 脚本：`model/bake_moe_expressions.py`。
- 命令：`<BLENDER> -b work/blenderProj/<slug>.blend --python tools/model/bake_moe_expressions.py -- --out-dir work/<ck>_anim_bake --out-blend work/blenderProj/<ck>_expression_rig.blend`
- 验收：
  - 重建率 67.6~99.3%（单向单骨只有 24.7%）；每顶点权重和恒为 1。
  - `python tools/verify/verify_expression_life.py --chars <ck>`：眨眼、位移、说话的可见性。
  - `verify/verify_expression_bake.py`、`measure_expression_fidelity.py`、`compare_shapekey_vs_bake.py`：Blender 后台脚本，对照真实 shapekey。
- 常见失败：表情不明显是量级错不是逻辑错（Blender Z 向上、跨模型幅度差几倍、中性头零眨眼），结构校验全过也看不出，要量数值；4 骨上限会静默截断，权重按语义保底。

## S4 动画：重定向、嫁接、注册

- 输入：S3 的 `work/<ck>_anim_bake/`，原版动画（读 CK3 安装目录）。
- 输出：`moe_body_*.anim`（torso，约 345 条）、`moe_head_*.anim`（head，约 357 条），再把完整 state 表写进 `.asset`。
- 命令（`import_chars.py` 把前两条串起来）：
  ```
  python tools/anim/build_all_anims.py --part torso --out-dir work/<ck>_anim_bake --rest-mesh work/<ck>_anim_bake/moe_torso.mesh --out-prefix moe_
  python tools/anim/graft_all_head_anims.py --out-dir work/<ck>_anim_bake --head-mesh work/<ck>_anim_bake/moe_head.mesh --out-prefix moe_
  python tools/anim/register_all_anims.py --out-prefix moe_<ck>_ --set-name moe_common_<ck> --head-entity moe_<ck>_head_entity --torso-entity moe_<ck>_torso_entity
  python tools/build/import_chars.py --ck <ck>       # 或 --pending
  ```
- 要点：
  - 换角色时三个脚本都要显式传该角色的目录、mesh、前缀，默认值只代表芙洛伦。
  - 鸣潮系游戏由 `import_chars.py` 自动给 torso 步加 `--foot-turnout --spine-up-rest --inherit-rot <report>`。
  - 单条调试用 `anim/retarget_vanilla_anim.py`（只能做 torso）和 `anim/graft_spine_into_head_anim.py`（只有 17 条情绪动画的清单）。
  - 不带 `--out-dir` 时写 `X.bake_dir(ck)`；对已发布角色那是不可变发布目录，见硬顺序第 8 条。
- 验收：
  - `python tools/verify/verify_all_anims.py --seams`：引用完整、骨数一致、head/torso 脊椎 rest 一致、循环接缝。
  - `python tools/verify/check_head_torso_sync.py --char <ck>`：头身脱节量。
  - `verify/check_anim_loop_seam.py`、`verify/verify_moe_anim.py`（纯二进制，能否被 CK3 吃下）、`verify/verify_retarget_root.py`、`verify/check_shared_bone_pose.py`。
  - 非循环动画首尾差是真实末端姿态，不能当接缝去消。
- 常见失败：
  - 日志刷 `Unknown animation state`：state 表没以原版 `.asset` 为清单，或 register 没跑。
  - 头脱开躯干 21.5 单位：head 没有一对一同名配 torso 动画。
  - 腰部打结/脊椎拧转：`body_root` 的旋转没一起传，或 PMX 局部轴带来脊椎滚转。
  - 手持道具错位：prop 骨只能建在躯干，道具要做双手关系 IK。

## S5 贴图

- 输入：源贴图（按 `moe_chars` 登记的 `src` 与 `tex` 正则找），`.asset` 的 shader 槽位。
- 输出：落在 `models/<char>_tex/` 的 diffuse/properties/normal，再转 DXT5 DDS。
- 命令：
  ```
  python tools/textures/build_char_textures.py --ck <ck>          # --all 为所有就绪角色
  python tools/textures/make_invisible_texture.py                 # 刻意不可见层用的全透明贴图
  python tools/textures/prepare_opacity_alpha_variants.py         # 按 shader 槽拆共享 alpha
  python tools/verify/verify_texture_alpha_contract.py            # 每个 diffuse 的 alpha 与槽位对得上
  python tools/textures/convert_textures_dds.py <目录...>
  ```
  这五条在 `build_moe_mod.py` 里按这个顺序被调用，一般不必手跑。
- 验收：
  - `.asset` 里的贴图引用必须是裸基名，带目录会全落空但画面看起来正常。
  - 二值化只对衣服 diffuse 做，每级 mip 都要重做；皮肤和眼的 alpha 不能二值化，判据看 shader 槽位而不是文件名。
  - 看 DDS 时间戳是否晚于 PNG；`convert_textures_dds.py` 曾只处理 `argv[1]`，构建返回 0 却整包没转。
- 常见失败：非 png/dds 的源（如 tga）会被落错包；properties 贴图 A=粗糙度、G=高光强，常量贴图出不来高光；虹膜粗糙度低于 0.32 会翻成两个白色正圆盘。

## S6 生成 mod 文本并部署

只能在沙盒里跑：先设 `MOE_OUT_ROOT`，不要对有人手改过的 `mod/` 跑。

- 输入：S2 到 S5 的全部产物，CK3 原版文件。
- 输出：`<OUT>/moe_core`、`moe_shader`、`moe_big_portrait`、各 `moe_models_<game>`，最后 `finalize_mods.py` 补 descriptor、自检、装机。
- 脚本：`build_all.py`（编排），内部按序调用：
  1. （仅 `--with-anims`）`anims_<ck>_torso`、`anims_<ck>_head`
  2. `build/build_moe_mod.py`（清空并重写；内部再调 S5 的贴图脚本与 `build_big_portrait_gui.py`）
  3. `build/build_moe_shader.py`（双面/卡通 shader 副本）
  4. `build/build_big_portrait_gui.py`（立绘放大相机 + gui 补丁）
  5. `build/build_moe_outline_shader.py`（必须在 gui 之后）
  6. `build/build_moe_lighting.py`（布光，注意文件要带 BOM）
  7. `build/build_moe_props.py`（手持道具 accessory 与基因）
  8. `build/build_moe_cosplay_gui.py`（换装窗口）
  9. `anim/register_all_anims.py`，芙洛伦一次，其余每个角色一次（按游戏分组并行，每个 `.asset` 只有一个写入者）
  10. `build/finalize_mods.py`

  `--from` 认的步骤名依次是：`mod`、`shader`、`gui`、`outline`、`lighting`、`props`、`cosplay_gui`、`register`、`register_<ck>`、`finalize`（开了 `--with-anims` 时最前面还有 `anims_<ck>_torso`、`anims_<ck>_head`）。
- 命令：
  ```
  MOE_OUT_ROOT=<沙盒目录> python tools/build_all.py --no-sync           # 不装机；末步 finalize 仍会写沙盒（物化动画、descriptor）
  python tools/build_all.py --from outline --no-sync                     # 从某一步开始（步骤名见上）
  python tools/build_all.py --with-anims                                 # 连动画重生成，约 15 分钟
  ```
- 验收：跑完会做产物自检，缺任何关键产物（portrait.shader、court_scene.shader、放大相机、换装窗口、布光、各角色道具、`.asset` 里 state 数 >500）就退出。自检失败是真问题，不要绕过去装机。
- 常见失败：
  - 日志有 `Unexpected token`：多半是 PDX 文本里有中文注释、`]]` 提前闭合代码块、或 BOM 读写不对称。
  - accessory 缺 `portrait_modifier` 一层会静默不渲染；基因名与 modifier 容器名是全局的，要自造名。
  - 同路径 `.asset` 会被后加载的 mod 整份覆盖，症状是只有 `Undefined entity`。
  - 新增顶层 window 引擎从不实例化，要挂进原版已有的容器。

## S7 物理烘焙

解算器是 C++（Bullet；另有 PhysX 试验路线），Python 侧读 PMX 和基础 `.anim`，写回物理版 `.anim`。

- 输入：PMX、`work/base_animation_inputs/<ck>/anims/`（从 canonical 无损复制并加前缀）、已编译的解算器 exe。
- 输出：先到隔离候选目录，验收后才纳入发布输入 `work/physics_release/<ck>/anims/`。
- 命令：
  ```
  powershell -ExecutionPolicy Bypass -File tools\physics\moe_bullet\build.ps1      # 编译 Bullet 解算器（需要 third_party/bullet3）
  python tools/physics/moe_physx/build.py --cpu-only                              # 可选：PhysX 路线，会下载固定提交的 SDK，需外网
  python tools/build/sync_base_animation_inputs.py --characters <ck> --jobs 8
  python tools/physics/moe_bullet_bake.py --ck <ck> --anim stayback_start --out-dir work/bullet_native/<ck> --debug-top 8
  python tools/physics/moe_bullet_bake.py --ck <ck> --all --jobs 12 --out-dir work/physics_candidates/<ck>
  ```
  `--all` 只跑登记进 `.asset` 的动画；`--backend` 可选 `bullet`、`physx-cpu`、`physx-gpu`。
- 辅助：
  - `physics/trial_response_batch.py`：隔离、可续跑的十动作批量试验，必须显式给 `--root`，不写发布资产。
  - `physics/make_trial_root.py`：用基础根加实验补丁造小试验根。
  - `physics/anim_shrink.py`：按容差摘通道并降帧，压体积。
  - `physics/open_physics_debug.py`：本地预览与单动作工作台，见下。
- 验收：
  - 先跑单条再全量。日志核对四行：`[bake]` 的循环判定（入场类应是“静置 N”，显示“循环×3”说明 `is_looping` 键没对上）、各组动态摆幅、分离率拉长峰、`[moe_bullet]` 的运动学/动态刚体数。
  - `python tools/verify/verify_phys_kick.py <ck> [anims_dir] --glob "*idle*"`：链级踢击指纹。
  - `physics/band_gate.py`、`ensemble_hf.py`、`ensemble_compare.py`：副本系综上的高频判据；混沌动作必须多份副本，用均值±2σ判差异。
  - `physics/passthrough_check.py`：裙子体是否穿到腿墙另一侧。
  - `verify/verify_phys_masks.py`、`verify/verify_physics_debug.py`、`verify/verify_physx_bake.py`、`verify/verify_physx_wind.py`：解算器契约测试。
- 纳入发布输入：代码里的入口是 `lib/moe_physics_release.py` 的 `adopt()`，命令行只有首次的 `--adopt-legacy`。后续修订如何纳入，我没在代码里找到专用的命令行，请以工程当前流程为准。
- 预览服务：
  ```
  MOE_PHYSICS_RESPONSE_TRIAL_VIEW=1 MOE_OUT_ROOT=<含模型的根> python tools/physics/open_physics_debug.py --read-only --only-source <来源名> --open
  ```
  它是本地 HTTP 服务，只绑 127.0.0.1，默认端口 8765；写操作靠每次启动随机生成的令牌，`--read-only` 直接禁写。没有账号体系，不要把它暴露到网络。首次需要 npm 装 three（脚本报错里有命令）。开 `/grid` 页看十动作。端口已被占用时用 `--port` 换。
- 常见失败：
  - 用 idle 测裙子物理量出 0.000°，因为躯干没动；先挑躯干会动的动画。
  - 碰撞组、碰撞盒朝向都必须从 PMX 移植，刚体要落座到当前帧姿态，否则互撞抽搐、装配错位。
  - 全量不是卡在 GPU，而是单进程串行写回；Windows 进程池不能从 stdin 起。
  - 批次进行中不要改进了代码指纹的文件，`--resume` 会让整批判失效。

## S8 1.20 载体架构转换（新包）

CK3 1.20 按骨骼下标缓存相机节点与共享姿态，所以真 mesh 做成配件，由一个只有退化三角形的骨架载体带动画。

- 输入：一个已按 S6 生成的模型包目录（例如沙盒里的 `moe_models_<包>`），在工程根目录下运行（路径 `work/bisect_...` 是相对当前目录的）。
- 输出：载体 mesh、合并动画 `<ck>/anims_merged/`、5 骨 rig、清理过的 `.asset`。原 `.asset` 备份到 `work/bisect_20261001/<包>.asset.before_split`。
- 命令：`python tools/build/carrier_pipeline.py <包目录>`，内部顺序：
  1. `split_portrait_carriers.py`（载体 + 真 mesh 配件）
  2. `inline_anim_sets.py`（把 `skeletal_animation_set` 展开成内联动画行）
  3. `merge_portrait_anims.py`（head 与 torso 动画按 state 合并）
  4. `apply_merged_anims.py`（state 指向合并动画）
  5. 头 mesh 按 torso 载体骨表 reskin（`build_portrait_carriers.py`）
  6. `build_cam_rig.py`（5 骨 rig）
  7. `prune_portrait_anims.py`（未引用动画移出，同盘重命名）
  8. `fix_eye_highlight.py`（Eyes+ 层贴回 eyeblend，去掉 portrait_decal）
- 之后（工程 `work/` 下）：`replay_pack_fixes.py <包目录>` → `vnode_fix_all.py <包名>` → `vnode_postcheck.py`。
- 验收：`vnode_postcheck` 零异常；包内 `moe_cam_rig23` 指向数等于 head pdxmesh 数。
- 常见失败：
  - 生成器产物不是已发布形态（缺异性 entity、儿童类型、地面道具等 6 项后处理），所以必须先 `replay_pack_fixes`。
  - prune 用 `os.replace`，沙盒在另一个盘时会失败。
  - 只对新包跑，并显式传包名，别碰已发布的包。

## S9 装机与实机验收

- 输入：S6 的产物目录。
- 输出：隔离 userdir（`work/ck3_florence_userdir`）里指向产物目录的 junction 与 `dlc_load.json`。
- 命令：
  ```
  python tools/build/finalize_mods.py [--no-sync] [--no-physics] [--jobs 12]
  powershell -ExecutionPolicy Bypass -File tools\build\launch_ck3.ps1 [-KeepLogs]
  ```
  `finalize_mods.py` 先校验来源并物化物理优先的最终动画，再写 descriptor、自检、装机；自检不过就拒绝装机。注意：即使传 `--no-sync`，它也会先改写输出根（用物理发布缓存替换模型包里的动画文件、写 descriptor 与缩略图、写 `work/runtime_animation_selection.json`），所以只能对沙盒或可丢弃的拷贝运行，不要对有人手改过的 `mod/` 运行。`--no-physics` 从基础烘焙输入恢复无物理对照，正常 finalize 再跑一次即恢复物理版。`launch_ck3.ps1` 内部已带 `-debug_mode`，否则不写 `error.log`。
- 验收（进游戏后，等日志停止增长再查，用 `grep -a`）：
  ```
  L="work/ck3_florence_userdir/logs"
  for k in "Failed to create material" "PlayAnimation failed" "Unknown animation state" "Missing default_state"; do echo "$k: $(grep -aci "$k" "$L/error.log")"; done   # 四项都必须是 0
  python tools/verify/final_verify.py --prefix moe_<ck>_
  python tools/verify/verify_asset.py --asset <.asset 路径>      # 进游戏闸：state 引用完整 + 骨数严格相等
  ```
  判断 mod 是否挂载看 `debug.log` 的 `Mounted Data:.*moe_` 与 `Folder not found`，不要看 `Enabled`。
- 常见失败：
  - 查日志前先确认进程启动时间晚于改动，否则会把旧日志的 0 错误当成修复成功；`Get-Process` 在 Git Bash 下的空结果不可信。
  - 强杀 CK3 会留下截断的 shader 缓存，下次启动刷出几万行假报错；看报错路径：指向 shadercache 是缓存坏，指向 `.shader` 带行号才是产物错。
  - 截图前先确认前台窗口是游戏，抓前台的工具会静默抓到别的程序。
  - 可选包用 `build/install_optional_mod.py`：默认只装不启用，`--enable` 要显式，`--restore` 可还原加载顺序。
- 目视辅助：`render/render_anim_textured.py`、`render_anim_skinned.py`、`open_anim_viewer.py`（Blender 后台渲图）。本地渲图看不见骨数不匹配、缺 meshsettings、缺 `portrait_modifier`，这些只在游戏日志里。

## S10 发布

- 脚本：`build/publish_mods.py`：自己只做 `finalize_mods.checks()` 自检，通过后把基础包整目录替换拷到目标目录，省掉未引用动画，外层 `.mod` 写绝对路径供启动器认。它假定输出根里 descriptor 和缩略图已由 `finalize_mods.py` 写好，但本脚本不会替你跑 finalize。读的是 `MOE_OUT_ROOT` 指向的输出根（默认 `mod/`），写的是 `--dest`。
- 命令：`python tools/build/publish_mods.py [--dest <目录>] [--jobs 12] [--only moe_core ...]`
- 上传到创意工坊不在本包范围，这里只写通用流程：
  - Core 走“暂存目录 + 内容清单”，上传前先升 descriptor 版本号。
  - 内容清单里不写 description，否则会覆盖线上描述。
  - 模型包每个数 GB，单独确认；原模型若禁止二次配布，上传前要提醒所有者。
  - 发布或重建前，先把工坊上的最新内容与仓库逐文件比对，工坊比仓库新时从仓库发布会静默回滚。
  - 登录凭据由所有者自己输入，不要交给助手。
- 常见失败：`DEFAULT_DEST` 写死了本机文档目录，换机器必须改。

---

## 附：本包的 import 抽查

在系统临时目录里复制一份本包的 `tools/`，用占位的盘点 JSON 补齐 `moe_chars.py` 要读的 105 条记录，只做 `python -I -B` 的 import（没有运行任何构建、烘焙、装机逻辑）：

- 80 个模块通过，全部解析到本包副本内，没有解析到包外。
- 4 个未通过，都不是 import 解析问题：`apply_merged_anims.py`、`prune_portrait_anims.py`、`inline_anim_sets.py` 是没有 `__main__` 保护的命令行脚本，导入即读 `sys.argv`；`verify/final_verify.py` 导入时就要读真实的 mesh。
- 没有占位记录时，几乎所有入口都会在 `moe_chars.py` 第 71 行抛 `FileNotFoundError`，这是缺数据，不是缺模块。
- 刻意没导入：`build/build_moe_mod.py`（导入即清空 mod 目录）、`model/build_florence_poc.py` 与 `model/bake_moe_expressions.py`（需要 Blender 的 bpy）。
- 如实说明：抽查名单里有两个脚本没有 `__main__` 保护，导入时就执行了各自的主体。`model/compare_pmx_skeletons.py` 在临时沙盒的占位盘点数据上跑完，写出的对比文件落在沙盒里，沙盒已删除，工程里原有的 `comparison.json` 时间戳没变。`build/survey_props_chain.py` 只读扫描了 CK3 安装目录并向 stdout 打印 JSON，不写文件。所有写入都限制在系统临时目录。`apply_merged_anims.py`、`prune_portrait_anims.py`、`inline_anim_sets.py` 在读 `sys.argv` 处就失败了，没有执行到任何写操作。
- `build/build_moe_shader.py` 以 UTF-8 BOM 开头，`ast.parse` 直接解析会报 U+FEFF，Python 正常 import 不受影响；这是原仓库的状态，未改动。
