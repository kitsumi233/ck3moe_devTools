# MANIFEST：收录文件清单

共 183 个文件（Python 149，PowerShell 2，其余 32 个是配套的 C++ 源码、CMake、JSON 配置和调试器前端）。全部是原工程文件的逐字节拷贝，没有改动内容。

列说明：
- 原始路径：相对工程根；包内位置与之相同（`tools/...`）。
- 用途：取自脚本自己的模块说明第一段；个别无说明的人工补了一句。
- 阶段：对应 `PIPELINE.md` 的步骤编号。
- 依赖：外部程序、数据目录。“硬编码”表示脚本里写死了盘符或本机路径，没有改写，换机器要自己改。
- 所有读取 `work/`、`mod/`、`assets/` 的脚本都需要工程里的对应数据，这些数据不在本包内。

通用外部依赖：Python 3.13；多数 Blender 脚本要 Blender 5.2（含 mmd_tools 与打过容差补丁的 io_pdx_mesh）；读原版文件的脚本要 CK3 安装目录；`tools/lib/moe_chars.py` 要 `work/pmx_skeleton_inventory/*.json`（缺则几乎所有入口 import 失败）。

## S0 环境与自审（1）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/audit_repo.py` | 仓库自审：从**文档声明的入口**出发算 tools/ 的可达闭包，其余列为候选。 | S0 审计 | 工程 work/ 下的烘焙数据 |

## S1 盘点 PMX 骨架（4）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/build/import_chars.py` | 批量接入角色：按 moe_chars 登记表把一个或多个角色从 PMX 推到「动画齐备、可被 build_all 部署」的状态。**不含物理**（用户 2026-09-06：批量角色先不烘物理）。 | S1-S4 编排 | Blender 5.2（作为子进程调用）；工程 work/ 下的烘焙数据；硬编码盘符 D: |
| `tools/model/compare_pmx_skeletons.py` | 汇总全部 PMX 骨架报告，做跨模型比较，判断能否抽象通用二次元骨架。 | S1 盘点 | 工程 work/ 下的烘焙数据 |
| `tools/model/inventory_gfl2_batch.py` | 批量盘点 assets/GFL2 下全部 PMX（每个独立 Blender 进程）。slug = gfl2_<pmx 英文名小写>。（被 build/import_chars.py 依赖而带入） | S1 盘点 | Blender 5.2（作为子进程调用）；工程 work/ 下的烘焙数据；硬编码盘符 D: |
| `tools/model/inventory_pmx_skeletons.py` | 导入单个 PMX，输出骨架/网格/材质/权重的结构化 JSON 报告。 | S1 盘点 | Blender 5.2（脚本内 bpy）；mmd_tools 插件 |

## S2 建 head/torso 网格（6）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/model/build_florence_poc.py` | 芙洛伦独立肖像实体 PoC：拆 head/torso、提取 deform 骨架、造 idle 动画、导出 PDX。（被 build/build_moe_mod.py 依赖而带入） | S2 建网格 | Blender 5.2（脚本内 bpy）；mmd_tools 插件；io_pdx_mesh 导出插件（需打容差补丁）；工程 work/ 下的烘焙数据 |
| `tools/model/mesh_bbox.py` | 量 PDX .mesh 的顶点包围盒与骨骼列表，用于和原版比尺度。 | S2 建网格 | 仅 Python 标准库与本包其他脚本 |
| `tools/model/probe_material_bones.py` | 探测每个材质的主导骨骼，判定它该挂 head 还是 torso/clothes 实体。 | S2 建网格 | Blender 5.2（脚本内 bpy）；mmd_tools 插件；工程 work/ 下的烘焙数据 |
| `tools/model/rename_mesh_object.py` | 改 PDX .mesh 里的对象名（`/object/<名字>` 那一层）。（被 build/build_moe_mod.py 依赖而带入） | S2 建网格 | 仅 Python 标准库与本包其他脚本 |
| `tools/verify/check_meshsettings_coverage.py` | 每个 submesh 都有 meshsettings 吗？—— 缺一条就有 5000 面走错 shader。 | S2 建网格 | 仅 Python 标准库与本包其他脚本 |
| `tools/verify/check_ownership.py` | 校验 head/torso mesh 的顶点是否划分正确（按顶点主导骨的父链归属）。 | S2 建网格 | 工程 work/ 下的烘焙数据 |

## S3 烘表情（9）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/model/bake_moe_expressions.py` | 把 shapekey 表情烘焙成 CK3 能吃的骨骼动画。Blender 后台脚本。（被 build/build_moe_mod.py 依赖而带入） | S3 表情 | Blender 5.2（脚本内 bpy）；io_pdx_mesh 导出插件（需打容差补丁）；numpy；工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/verify/compare_shapekey_vs_bake.py` | 对某条网格边，比较「骨骼烘焙重建」与「真实 shapekey 直接驱动」的分离量。 | S3 表情 | Blender 5.2（脚本内 bpy）；工程 work/ 下的烘焙数据 |
| `tools/verify/measure_expression_fidelity.py` | 量化表情烘焙的保真度：shapekey 真实位移 vs 烘焙后骨骼能重建的位移。（被 lib/moe_expression_contract.py 依赖而带入） | S3 表情 | Blender 5.2（脚本内 bpy）；工程 work/ 下的烘焙数据 |
| `tools/verify/verify_expression_bake.py` | 校验 2026-08-18 反向骨 + 双侧合并改造后的表情烘焙产物。Blender 后台脚本。（被 verify/compare_shapekey_vs_bake.py 依赖而带入） | S3 表情 | Blender 5.2（脚本内 bpy）；工程 work/ 下的烘焙数据 |
| `tools/verify/verify_expression_basis.py` | Regressions for coherent emotional-mouth and speech composition. | S3 表情 | numpy |
| `tools/verify/verify_expression_export.py` | Compare exported expression geometry with native Blender shape-key targets. | S3 表情 | Blender 5.2（脚本内 bpy）；io_pdx_mesh 导出插件（需打容差补丁）；numpy；工程 work/ 下的烘焙数据 |
| `tools/verify/verify_expression_fit.py` | Behavioral regressions for fixed-native, four-influence expression fitting. | S3 表情 | numpy |
| `tools/verify/verify_expression_life.py` | 量表情源与 head 产物里「活」的信号：眼球位移、眨眼次数、嘴张幅、接缝。 | S3 表情 | numpy；工程 work/ 下的烘焙数据 |
| `tools/verify/verify_expression_semantics.py` | 表情分派、真实 state 循环及缺槽降级的回归检查，不依赖 Blender。 | S3 表情 | 仅 Python 标准库与本包其他脚本 |

## S4 动画：重定向、嫁接、注册（15）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/anim/anim_channels.py` | 列出 .anim 里每根骨的采样通道（t/q/s），用于判定「谁驱动谁」。（被 anim/graft_spine_into_head_anim.py 依赖而带入） | S4 动画 | 仅 Python 标准库与本包其他脚本 |
| `tools/anim/anim_writeback.py` | `.anim` 写回底座：蒙皮归属、head/torso 配对、写回、接缝平滑、重采样。（被 physics/moe_bullet_bake.py 依赖而带入） | S4 动画 | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/anim/build_all_anims.py` | 生成**完整**动画集：把原版全部肖像动作/表情迁移到 moe 骨架。（被 build/build_moe_mod.py 依赖而带入） | S4 动画 | 工程 mod/ 目录 |
| `tools/anim/graft_all_head_anims.py` | 批量生成完整的 head 侧动画：表情通道 + 配对 torso 动作的脊椎通道。（被 build/build_moe_mod.py 依赖而带入） | S4 动画 | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/anim/graft_spine_into_head_anim.py` | 为每个身体动作生成配套的头部动画（表情 fx + 该动作的脊椎），修「头脱开躯干」。（被 anim/graft_all_head_anims.py 依赖而带入） | S4 动画 | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/anim/prepare_semantic_motion.py` | Stage gentle-v1 from verified, unsimulated release inputs before physics. | S4 动画 | numpy；工程 work/ 下的烘焙数据 |
| `tools/anim/register_all_anims.py` | 把**完整** state 表写进 moe_portrait.asset —— 完整覆盖原版全部肖像动画。（被 build_all.py 依赖而带入） | S4 动画 | 工程 work/ 下的烘焙数据 |
| `tools/anim/retarget_vanilla_anim.py` | 把原版 CK3 身体动作重定向到 moe 骨架，产出可直接用的 .anim。（被 physics/moe_bullet_bake.py 依赖而带入） | S4 动画 | numpy；CK3 安装目录；工程 work/ 下的烘焙数据；工程 mod/ 目录；硬编码盘符 E: |
| `tools/verify/audit_skinned_pose.py` | 按真实 .mesh + .anim 做 CPU 蒙皮，横向审计角色包的几何问题（纯 numpy，不需要 Blender）。 | S4 动画 | numpy；工程 mod/ 目录 |
| `tools/verify/check_anim_loop_seam.py` | 量 .anim 的首尾帧姿势差，判断「自跳转循环」会不会跳变。 | S4 动画 | 仅 Python 标准库与本包其他脚本 |
| `tools/verify/check_head_torso_sync.py` | 量 head 实体与 torso 实体在身体动作下的脱节量（绝对单位）。（被 anim/graft_spine_into_head_anim.py 依赖而带入） | S4 动画 | 仅 Python 标准库与本包其他脚本 |
| `tools/verify/check_shared_bone_pose.py` | head 与 torso 两份动画对同名骨给出的世界姿态差（引擎按骨名共享姿态，差值 = 画面上会跳/错位的量）。 | S4 动画 | numpy |
| `tools/verify/verify_all_anims.py` | 校验完整动画集：引用完整性 + 骨数一致 + 循环接缝。 | S4 动画 | 工程 mod/ 目录 |
| `tools/verify/verify_moe_anim.py` | 校验烘焙出的 .anim 是否真的能被 CK3 吃下。纯二进制解析，不依赖 Blender。 | S4 动画 | 工程 work/ 下的烘焙数据 |
| `tools/verify/verify_retarget_root.py` | Read-only source body-root gate; see moe_retarget_root for the contract.（被 lib/moe_retarget_root.py 依赖而带入） | S4 动画 | 仅 Python 标准库与本包其他脚本 |

## S5 贴图（6）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/textures/build_char_textures.py` | 任一角色的贴图落位 + properties / normal 生成（按 moe_chars 登记表驱动）。（被 build/build_moe_mod.py 依赖而带入） | S5 贴图 | numpy；Pillow；工程 work/ 下的烘焙数据 |
| `tools/textures/build_moe_properties_maps.py` | 把源模型的 RMO / normal 贴图重打包成 CK3 portrait shader 要的通道布局。（被 build/build_moe_mod.py 依赖而带入） | S5 贴图 | numpy；Pillow；工程 mod/ 目录 |
| `tools/textures/convert_textures_dds.py` | 把 mod 里的 PNG 贴图转成 CK3 能加载的 DDS。 | S5 贴图 | numpy；Pillow |
| `tools/textures/make_invisible_texture.py` | 生成一张全透明贴图，用来让某个子网格「注册但不可见」。（被 build/build_moe_mod.py 依赖而带入） | S5 贴图 | Pillow；工程 mod/ 目录 |
| `tools/textures/prepare_opacity_alpha_variants.py` | Split diffuse alpha by shader use before the texture contract check. | S5 贴图 | numpy；Pillow |
| `tools/verify/verify_texture_alpha_contract.py` | 按 shader 槽位审计每张 diffuse 的 alpha，抓「该二值化却没二值化」的漏网。 | S5 贴图 | numpy；Pillow |

## S6 生成 mod 文本并部署（13）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/build/build_big_portrait_gui.py` | 把「放大立绘」的能力合并进我们自己的 mod，从**原版文件**重新打补丁。（被 build_all.py 依赖而带入） | S6 生成 mod | CK3 安装目录；硬编码盘符 E: |
| `tools/build/build_moe_cosplay_gui.py` | 生成换 cos 服的大窗口（预览模型 + 姿势下拉）。（被 build_all.py 依赖而带入） | S6 生成 mod | CK3 安装目录 |
| `tools/build/build_moe_lighting.py` | 生成 mod 的肖像布光与后处理覆盖 —— 关掉角色自身投影与 SSAO。（被 build_all.py 依赖而带入） | S6 生成 mod | CK3 安装目录；硬编码盘符 E: |
| `tools/build/build_moe_mod.py` | 生成芙洛伦 PoC 的 CK3 mod 文件：portrait_type、asset、文化、genes、本地化。（被 lib/moe_cultural_units.py 依赖而带入） | S6 生成 mod | CK3 安装目录；工程 work/ 下的烘焙数据；工程 mod/ 目录；硬编码盘符 C:/D:/E:；硬编码本机用户目录 |
| `tools/build/build_moe_outline_shader.py` | 从原版 jomini/gui_portrait.shader 生成带黑色描边的 moe 版本。（被 build_all.py 依赖而带入） | S6 生成 mod | CK3 安装目录；硬编码盘符 E: |
| `tools/build/build_moe_props.py` | 给 moe 各 group 生成手持道具（accessory）支持。（被 lib/moe_anim_catalog.py 依赖而带入） | S6 生成 mod | CK3 安装目录；硬编码盘符 E: |
| `tools/build/build_moe_shader.py` | 双面 / 卡通 shader 副本，加王座厅场景 shader（输出到 moe_shader 包）（被 lib/moe_material_contract.py 依赖而带入） | S6 生成 mod | CK3 安装目录；硬编码盘符 E: |
| `tools/build/survey_props_chain.py` | 读原版 props 链，JSON 打到 stdout（build_moe_mod / build_moe_props 调用）（被 build/build_moe_props.py 依赖而带入） | S6 生成 mod | CK3 安装目录；硬编码盘符 E: |
| `tools/build_all.py` | 按正确顺序跑完整套构建，然后拆分 + 同步。（被 build/build_moe_mod.py 依赖而带入） | S6 编排 | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/verify/verify_build_io.py` | Compare fast metadata with full parsing and check parallel deployment fidelity. | S6 生成 mod | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/verify/verify_cosplay_rules.py` | Exercise generated character reconciliation rules without mutating game saves. | S6 生成 mod | CK3 安装目录；工程 mod/ 目录；硬编码盘符 E: |
| `tools/verify/verify_moe_age_types.py` | Audit age-aware portrait type output without starting the game. | S6 生成 mod | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/verify/verify_wardrobe.py` | Offline execution of emitted wardrobe rules, not a CK3 engine substitute. | S6 生成 mod | CK3 安装目录；硬编码盘符 E: |

## S7 物理烘焙（86）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/build/sync_base_animation_inputs.py` | Refresh work/base_animation_inputs from canonical bake output only.（被 lib/moe_paths.py 依赖而带入） | S7 物理输入 | 工程 work/ 下的烘焙数据 |
| `tools/physics/action_labels_cn.json` | 调试器动作中文名补充表 | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/anim_shrink.py` | Shrink baked physics .anim files: tolerance channel drop + 30 -> 15 fps. | S7 物理 | numpy |
| `tools/physics/band_gate.py` | Perceptual jitter gate: secondary motion above 0.5 Hz, per named region. | S7 物理 | numpy |
| `tools/physics/bone_twist.py` | Bone-local longitudinal twist contract, independent of PMX cross-links.（被 physics/physics_response.py 依赖而带入） | S7 物理 | numpy |
| `tools/physics/bone_twist_partial_preview.py` | Hash-verified, read-only preview for the user-paused all-role candidate.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/bone_twist_preview.py` | Hash verified read only viewer for the longitudinal bone Z lock candidate.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/debug_ui/app.js` | 物理预览前端（open_physics_debug 以静态文件提供） | S7 物理 | 浏览器 + three@0.180.0（npm） |
| `tools/physics/debug_ui/grid.html` | 物理预览前端（open_physics_debug 以静态文件提供） | S7 物理 | 浏览器 + three@0.180.0（npm） |
| `tools/physics/debug_ui/grid.js` | 物理预览前端（open_physics_debug 以静态文件提供） | S7 物理 | 浏览器 + three@0.180.0（npm） |
| `tools/physics/debug_ui/index.html` | 物理预览前端（open_physics_debug 以静态文件提供） | S7 物理 | 浏览器 + three@0.180.0（npm） |
| `tools/physics/debug_ui/style.css` | 物理预览前端（open_physics_debug 以静态文件提供） | S7 物理 | 浏览器 + three@0.180.0（npm） |
| `tools/physics/ensemble_compare.py` | Compare ensemble_hf result files family by family (mean±std, 2σ rule). | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/ensemble_hf.py` | Replicate-aware HF gate for isolated response trials.（被 physics/band_gate.py 依赖而带入） | S7 物理 | numpy |
| `tools/physics/explicit_bake_inputs.py` | Normal native bake API with explicit candidate meshes and animation sources.（被 physics/trial_response_batch.py 依赖而带入） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/expression_preview.py` | Read-only access to the twelve audited expression-v2 preview candidates.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据 |
| `tools/physics/gate_all92_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_all92h_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_all92r_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_allfix_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_c60_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_c60dx_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_c60dxg_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_c60dxgl_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_c60dxv_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_candidate_preview.py` | Hash verified read only viewer for a fast-gate 10-action trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_cloud_preview.py` | Hash verified read only viewer for the cloud FULL92 re-bake, shrunk (15 fps) anims. The review root is built b（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 D: |
| `tools/physics/gate_control_preview.py` | Hash verified read only viewer for a fast-gate 10-action trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_final_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_full_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_fx_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_main_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 D:/F: |
| `tools/physics/gate_old92_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_palm_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/gate_soft1_preview.py` | Hash verified read only viewer for the fast-gate 60 Hz compliant-contact trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/lau_response_preview.py` | Hash-verified read-only access to the Lau ten-action tuning trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/lau_sitting_trials_preview.py` | Hash-verified, read-only Lau sitting variants for the physics viewer.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/leg_skin_fit.py` | Fit kinematic thigh capsules to the real skinned leg (2026-10-11, 桃祈/洛可可坐姿裙被腿夹).（被 physics/trial_response_batch.py 依赖而带入） | S7 物理 | mmd_tools 插件；numpy |
| `tools/physics/make_trial_root.py` | Create a small isolated trial root from a base root with an experiment patch. | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/moe_bullet/CMakeLists.txt` | Bullet 解算器 CMake | S7 物理 | MSVC + CMake |
| `tools/physics/moe_bullet/build.ps1` | 编译 moe_bullet_bake.exe 到 work/moe_bullet_build/。Bullet 静态库一次编好后增量很快。（被 physics/moe_bullet_bake.py 依赖而带入） | S7 物理 | 原生解算器 exe（先编译）；工程 work/ 下的烘焙数据；硬编码盘符 C: |
| `tools/physics/moe_bullet/main.cpp` | Bullet 解算器 C++ 源；编译需要第三方库 third_party/bullet3 | S7 物理 | MSVC + CMake |
| `tools/physics/moe_bullet_bake.py` | 原生 Bullet 物理烘焙的 Python 侧：读 PMX 与 .anim → 喂 moe_bullet_bake.exe → 写回 .anim。（被 verify/verify_phys_masks.py 依赖而带入） | S7 物理 | Blender 5.2（作为子进程调用）；mmd_tools 插件；numpy；原生解算器 exe（先编译）；工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/physics/moe_physx/CMakeLists.txt` | PhysX 解算器 CMake | S7 物理 | MSVC + CMake |
| `tools/physics/moe_physx/build.py` | Build the pinned native PhysX prototype on Windows (no Python package install).（被 physics/moe_bullet_bake.py 依赖而带入） | S7 物理 | 原生解算器 exe（先编译）；工程 work/ 下的烘焙数据 |
| `tools/physics/moe_physx/main.cpp` | PhysX 解算器 C++ 源；SDK 由 build.py 按固定提交与 SHA256 下载 | S7 物理 | MSVC + CMake |
| `tools/physics/moe_physx/twist_z.h` | PhysX 解算器的 Z 轴锁定头文件 | S7 物理 | MSVC + CMake |
| `tools/physics/open_physics_debug.py` | 本地 HTTP 预览/单动作 Bullet 工作台，只绑 127.0.0.1，无鉴权；首次需 npm 装 three（见脚本报错提示） | S7 物理 | numpy；Pillow；工程 work/ 下的烘焙数据；工程 mod/ 目录；硬编码盘符 E: |
| `tools/physics/passthrough_check.py` | Detect skirt bodies that tunnelled to the wrong side of a leg wall. | S7 物理 | numpy |
| `tools/physics/physics_profile.py` | Validated, reproducible overrides for interactive native Bullet experiments.（被 physics/moe_bullet_bake.py 依赖而带入） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/physics_release_recipe.py` | Persistent source physics recipes with isolated per-action native options.（被 physics/moe_bullet_bake.py 依赖而带入） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/physics_response.py` | Candidate-only, unit-explicit secondary-motion response design.（被 physics/trial_response_batch.py 依赖而带入） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/cha.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/chs.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/dai.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/das.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 硬编码盘符 D: |
| `tools/physics/release_recipes/flo.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/gro.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/grs.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/grz.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/jia.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/kse.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/lec.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/len.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/mab.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/mac.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/nem.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/per.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 硬编码盘符 D: |
| `tools/physics/release_recipes/pri.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/qio.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/sha.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/release_recipes/tol.json` | 角色物理发布配方（physics_release_recipe 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/response_preview.py` | Hash-verified read-only access to the 92-character response trial.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/response_release.json` | 试验根发布配置（make_trial_root 读取） | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/physics/response_trial_review_preview.py` | Hash-verified read-only viewer source for the representative trial review.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/sample_review.py` | Read-only, hash-bound access to the five user-review samples.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据 |
| `tools/physics/trial_artifact_cache.py` | Shared integrity checks and content keys for isolated physics trials.（被 physics/trial_response_batch.py 依赖而带入） | S7 物理 | numpy |
| `tools/physics/trial_response_batch.py` | Isolated, resumable ten-action response trial across registered characters. | S7 物理 | numpy |
| `tools/physics/twist_z_lock_preview.py` | Read-only five-role zero local Z twist candidate, with immutable file hashes.（被 physics/open_physics_debug.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/twist_z_preview.py` | Hash-verified five-role local Z twist candidate; no release writes.（被 physics/wuwa_preview.py 依赖而带入） | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/physics/wuwa_preview.py` | 只读预览提供者；证据根硬编码在暂存盘 | S7 物理 | 工程 work/ 下的烘焙数据；硬编码盘符 F: |
| `tools/verify/verify_phys_kick.py` | 物理产物输出校验：按**骨链**统计踢击率，超阈值的链列出并给指纹。 用法：python tools/verify/verify_phys_kick.py <ck> [anims_dir] [--glob "*idle* | S7 物理 | numpy |
| `tools/verify/verify_phys_masks.py` | Verify collision exclusions survive the per-animation arm-group remapping. | S7 物理 | 仅 Python 标准库与本包其他脚本 |
| `tools/verify/verify_physics_debug.py` | Validate profile isolation, capsule repair, and native input rejection. | S7 物理 | 工程 work/ 下的烘焙数据 |
| `tools/verify/verify_physx_bake.py` | Physical/IO contract checks for the optional native PhysX baker; evidence is retained.（被 verify/verify_physx_wind.py 依赖而带入） | S7 物理 | numpy；原生解算器 exe（先编译）；工程 work/ 下的烘焙数据 |
| `tools/verify/verify_physx_wind.py` | Check wind units, dynamic-only force, per-instance phase and short loop continuation. | S7 物理 | numpy |

## S8 1.20 载体架构转换（8）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/build/apply_merged_anims.py` | Point torso carriers at the merged head+torso anims. | S8 载体 | 工程 work/ 下的烘焙数据 |
| `tools/build/build_portrait_carriers.py` | Skeleton carriers for the moe portrait entities (CK3 1.20).（被 build/split_portrait_carriers.py 依赖而带入） | S8 载体 | numpy；CK3 安装目录 |
| `tools/build/carrier_pipeline.py` | Convert one model pack to the carrier architecture (same steps gfl2 went through, in order). | S8 载体 | 工程 work/ 下的烘焙数据 |
| `tools/build/fix_eye_highlight.py` | Eyes+ (eye highlight layer) back on; face/outfit entity game_data back to interval 0 1, no decal. | S8 载体 | 仅 Python 标准库与本包其他脚本 |
| `tools/build/inline_anim_sets.py` | Inline every skeletal_animation_set into the pdxmesh that imports it (carriers carry their own anim list). | S8 载体 | 仅 Python 标准库与本包其他脚本 |
| `tools/build/merge_portrait_anims.py` | Merge each torso state's anim with its paired head anim, so the torso carrier alone drives every bone.（被 build/apply_merged_anims.py 依赖而带入） | S8 载体 | 工程 work/ 下的烘焙数据 |
| `tools/build/prune_portrait_anims.py` | Drop animation registrations no state uses, then move the unreferenced .anim files out of the mod. | S8 载体 | 仅 Python 标准库与本包其他脚本 |
| `tools/build/split_portrait_carriers.py` | Split every moe head/torso of a model pack into skeleton carrier + real mesh accessory. | S8 载体 | 仅 Python 标准库与本包其他脚本 |

## S9 装机与验收（9）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/build/finalize_mods.py` | 收尾：给直写出来的各子 mod 补 descriptor、自检、装进隔离 userdir。（被 verify/verify_build_io.py 依赖而带入） | S9 装机 | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/build/install_optional_mod.py` | Install one optional Moe mod into the isolated CK3 userdir. | S9 装机 | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/build/launch_ck3.ps1` | 用隔离 userdir 启动 CK3。 | S9 装机 | CK3 安装目录；硬编码盘符 E: |
| `tools/render/open_anim_viewer.py` | 把一条**写回后的 .anim** 加载到骨架上，用于在 Blender GUI 里看成品效果。 | S9 目视 | Blender 5.2（脚本内 bpy）；工程 work/ 下的烘焙数据 |
| `tools/render/render_anim_skinned.py` | 在 Blender 里带蒙皮渲染 .anim，用来查穿插与皮肤撕裂。（被 verify/final_verify.py 依赖而带入） | S9 目视 | Blender 5.2（脚本内 bpy）；io_pdx_mesh 导出插件（需打容差补丁）；工程 work/ 下的烘焙数据；硬编码盘符 D: |
| `tools/render/render_anim_textured.py` | 带材质渲染部署的 .anim —— 用来肉眼确认头发/裙子物理。 | S9 目视 | Blender 5.2（脚本内 bpy）；mmd_tools 插件；numpy；工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/verify/final_verify.py` | 全量上线前校验。每一条都对应过去踩过的一个坑： 骨数一致 —— 不匹配会让整个 entity 静默不渲染，本地渲图看不见 头身帧数一致 —— 不一致头身脱节 共享骨 <2° —— 合并骨架特有问题（原版头身骨骼不相交所以 | S9 验收 | 仅 Python 标准库与本包其他脚本 |
| `tools/verify/measure_face_shading.py` | 量脸部明暗分布 —— **面积比不是有效判据，读 docstring 再用**。（被 build/build_moe_shader.py 依赖而带入） | S9 验收 | numpy；Pillow；工程 work/ 下的烘焙数据 |
| `tools/verify/verify_asset.py` | 进游戏前的两条硬判据（都是本地渲图看不见、只会在日志里报的问题）。（被 build/build_moe_mod.py 依赖而带入） | S9 验收 | CK3 安装目录；硬编码盘符 E: |

## S10 发布（1）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/build/publish_mods.py` | 发布：把仓库 mod/ 里的 7 个基础包拷到常规文档 mod 目录，供启动器上传创意工坊。 | S10 发布 | CK3 安装目录；工程 mod/ 目录；硬编码盘符 C:；硬编码本机用户目录 |

## 底座：被 import 的公共模块（25）

| 原始路径 | 用途 | 阶段 | 依赖 |
|---|---|---|---|
| `tools/lib/moe_anim_catalog.py` | 完整动画清单：原版 state 表 → 我们要生成哪些 .anim、每个用哪个原版源。（被 lib/moe_retarget_root.py 依赖而带入） | 底座 | CK3 安装目录；硬编码盘符 E: |
| `tools/lib/moe_anim_observability.py` | Explicit destination-mesh dependencies for lossless animation sample export.（被 physics/trial_response_batch.py 依赖而带入） | 底座 | 仅 Python 标准库与本包其他脚本 |
| `tools/lib/moe_base_inputs.py` | Unsimulated animation cache for physics input and base/current comparison.（被 lib/moe_paths.py 依赖而带入） | 底座 | 工程 work/ 下的烘焙数据 |
| `tools/lib/moe_bone_contract.py` | 二次元 deform 语义骨架合同：日文 MMD 骨名 → ASCII 语义名。（被 physics/moe_bullet_bake.py 依赖而带入） | 底座 | 工程 work/ 下的烘焙数据；硬编码盘符 D: |
| `tools/lib/moe_chars.py` | 角色登记表 —— **加角色只改这个文件**，其余一切派生。（被 verify/verify_phys_masks.py 依赖而带入） | 底座 | 工程 work/ 下的烘焙数据 |
| `tools/lib/moe_cosplay_rules.py` | Cultural-tradition eligibility and reconciliation for the normal mod builder.（被 verify/verify_cosplay_rules.py 依赖而带入） | 底座 | 仅 Python 标准库与本包其他脚本 |
| `tools/lib/moe_cultural_units.py` | Script/localization sources of the optional moe_cultural_units package.（被 verify/verify_cosplay_rules.py 依赖而带入） | 底座 | 工程 work/ 下的烘焙数据 |
| `tools/lib/moe_culture_ethnicities.py` | Generate character-scope ethnicity restoration from vanilla CK3 data.（被 verify/verify_cosplay_rules.py 依赖而带入） | 底座 | 仅 Python 标准库与本包其他脚本 |
| `tools/lib/moe_expression_basis.py` | Native-key contract for composing animations whose mouth proxies are shared.（被 verify/verify_expression_basis.py 依赖而带入） | 底座 | numpy |
| `tools/lib/moe_expression_closure.py` | Select native blink contacts from geometry without character-specific IDs.（被 verify/verify_expression_export.py 依赖而带入） | 底座 | numpy |
| `tools/lib/moe_expression_contract.py` | 二次元表情/动作语义合同：跨模型复用的接口层。（被 verify/verify_expression_export.py 依赖而带入） | 底座 | 仅 Python 标准库与本包其他脚本 |
| `tools/lib/moe_expression_fit.py` | Fit native expression fields within the actual four-influence skin budget.（被 verify/verify_expression_fit.py 依赖而带入） | 底座 | numpy |
| `tools/lib/moe_material_contract.py` | 材质 → CK3 部件（head / torso / clothes / drop）的分类合同。（被 build/finalize_mods.py 依赖而带入） | 底座 | mmd_tools 插件；工程 work/ 下的烘焙数据；硬编码盘符 D: |
| `tools/lib/moe_out.py` | 产物归属的唯一真值：谁写文件，谁当场声明它属于哪个 mod。（被 anim/anim_writeback.py 依赖而带入） | 底座 | 工程 mod/ 目录 |
| `tools/lib/moe_paths.py` | moe mod 的资源路径集中定义。**改目录结构只改这一个文件。**（被 physics/moe_bullet_bake.py 依赖而带入） | 底座 | 工程 work/ 下的烘焙数据；工程 mod/ 目录 |
| `tools/lib/moe_physics_release.py` | Keep verified physics inputs in work/ and publish one animation per model path.（被 build/finalize_mods.py 依赖而带入） | 底座 | 工程 work/ 下的烘焙数据 |
| `tools/lib/moe_pmx_inherit.py` | PMX 的「回転付与」骨表 → 我们的骨名（retarget 的 --inherit-rot 用）。（被 anim/retarget_vanilla_anim.py 依赖而带入） | 底座 | mmd_tools 插件 |
| `tools/lib/moe_pose_catalog.py` | 换装窗口的姿势清单 —— 从原版 animations.txt **全部顶层块**自动派生。（被 build/build_moe_mod.py 依赖而带入） | 底座 | 仅 Python 标准库与本包其他脚本 |
| `tools/lib/moe_release_sources.py` | Explicit immutable release inputs; an absent active pointer preserves legacy paths.（被 lib/moe_chars.py 依赖而带入） | 底座 | 工程 work/ 下的烘焙数据 |
| `tools/lib/moe_retarget_root.py` | Shared read-only body-root contract for unsimulated retargeted animations.（被 lib/moe_base_inputs.py 依赖而带入） | 底座 | numpy；工程 work/ 下的烘焙数据 |
| `tools/lib/moe_semantic_motion.py` | Opt-in, pre-physics torso motion; head channels must be grafted afterwards.（被 anim/retarget_vanilla_anim.py 依赖而带入） | 底座 | numpy |
| `tools/lib/moe_wardrobe.py` | Saved wardrobe rules; emitted by the existing mod and GUI builders.（被 build/build_moe_mod.py 依赖而带入） | 底座 | 仅 Python 标准库与本包其他脚本 |
| `tools/lib/pdx_anim_write.py` | 写 PDX `.anim` 二进制。项目原先只有解析器（pdx_parse.py），没有写出器。（被 anim/anim_writeback.py 依赖而带入） | 底座 | io_pdx_mesh 导出插件（需打容差补丁） |
| `tools/lib/pdx_parse.py` | 正确解析 PDX .mesh / .anim 二进制结构。（被 physics/moe_bullet_bake.py 依赖而带入） | 底座 | io_pdx_mesh 导出插件（需打容差补丁） |
| `tools/lib/win_capture.py` | Windows 窗口截图 / 裁剪 / 连拍 / 送输入。**截图相关的唯一实现**。 | 底座 | Pillow；工程 work/ 下的烘焙数据 |

## 硬编码路径汇总

51 个文件含硬编码盘符或本机路径，逐条见上表“依赖”列。归纳：

- `D:`：Blender 安装路径与工程根目录。
- `E:`：CK3 安装目录；`open_physics_debug.py` 里还有一批物理证据目录。
- `F:`：暂存盘，物理试验的证据根（几乎全部在 `physics/*_preview.py`）。
- `C:`：本机用户文档下的 CK3 mod 目录（`build_moe_mod.py`、`publish_mods.py`），Visual Studio Build Tools 路径（`moe_bullet/build.ps1`）。
- `physics/release_recipes/das.json`、`per.json` 里各有一处指向旧证据目录的绝对路径。
