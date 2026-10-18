# TASK S1+S2：盘点 PMX 骨架，建 head/torso 网格

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_1_model_expression_textures.md`。把尖括号里的内容换成实际值。

## 提示词

你要把角色 `<ck>`（slug：`<slug>`，源模型：`<PMX 路径>`）从 PMX 推到可用的 head/torso 网格。只写 `work/` 下的目录，不碰 `mod/`。

### 先读

- `PIPELINE.md` 的 S1、S2 两节。
- `LESSONS_1` 的“头身拆分与材质归属”“骨架与骨数”两节。
- `tools/lib/moe_chars.py` 里相邻角色的登记行，照格式加 `<ck>`（只加一行，列表顺序就是槽位号，只能往后追加）。

### 步骤

1. 盘点：`python tools/model/inventory_gfl2_batch.py`（批量），或设置环境变量 `MOE_PMX_PATH`、`MOE_PMX_REPORT` 后用 Blender 后台跑 `tools/model/inventory_pmx_skeletons.py`。确认 `work/pmx_skeleton_inventory/<slug>.json` 的 `source_file` 指向正确的 PMX。
2. 如需与已有角色比对骨架：`python tools/model/compare_pmx_skeletons.py`。它会写 `work/pmx_skeleton_inventory/comparison.*`，先确认可以覆盖。
3. 建网格：`<BLENDER> -b --python tools/model/build_florence_poc.py -- --model <slug>`。
4. 逐道看日志里的三道归属校验（STEP 2 材质名，2b 几何高度，2c 骨骼父链）。2c 的 `判不出` 必须接近 0。
5. 校验：`python tools/verify/check_meshsettings_coverage.py`、`python tools/verify/check_ownership.py`。

### 验收（逐项给出实测值）

- 产出 `work/<slug>/moe_head.mesh`、`moe_torso.mesh`、`<slug>_report.json` 与 `work/blenderProj/<slug>.blend`。
- 归属校验：head 里躯干骨主导的顶点为 0；`判不出` 接近 0。
- 每个 submesh 都有 meshsettings。
- 头身沿脖子线干净分开。材质名会骗人，必须按顶点主导骨的父链复核。
- “身体没了”先查 clothes，torso 可能只有一千多个面。

### 不要做

- 不要改脚本里的路径或模型名，用 `--model <slug>`。
- 不要为了让校验通过去放宽阈值，先找真因。
- 不要把 `判不出` 当成“没问题”：它大说明骨名空间不匹配，校验会假阴性。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：head/torso 面数与骨数、归属校验三项的实测值、被分到别处的材质清单。
