# TASK S3：烘表情

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_1_model_expression_textures.md`。

## 提示词

你要给角色 `<ck>`（slug：`<slug>`）把 MMD 形态键表情烘成骨骼动画。CK3 动画只吃骨骼通道，没有 morph，所以表情走“形态键 → 代理骨”这条路，不能走骨骼重定向（MMD 骨架没有脸骨）。只写 `work/`。

### 先读

- `PIPELINE.md` 的 S3 一节。
- `LESSONS_1` 的“表情烘焙”一节，特别是权重归一化、4 骨上限、量级三问题。
- `tools/lib/moe_expression_contract.py` 的模块说明：表情与动作的语义合同。

### 步骤

1. 前置：S2 已完成，`work/blenderProj/<slug>.blend` 存在（芙洛伦的是 `florence_poc.blend`）。
2. 烘焙：
   ```
   <BLENDER> -b work/blenderProj/<slug>.blend --python tools/model/bake_moe_expressions.py -- --out-dir work/<ck>_anim_bake --out-blend work/blenderProj/<ck>_expression_rig.blend
   ```
3. 可见性：`python tools/verify/verify_expression_life.py --chars <ck>`。
4. 保真度（Blender 后台）：`verify/verify_expression_bake.py`、`verify/measure_expression_fidelity.py`、`verify/compare_shapekey_vs_bake.py`。读它们的模块说明再决定参数。

### 验收（逐项给出实测值）

- `work/<ck>_anim_bake/bake_report.json`：看 `export` 全部 ok、`missing_slots`、`slot_fallbacks`。
- 重建率应在 67.6~99.3% 一带；低于这个范围先查正反双骨与双侧合并，不要先改阈值。
- 每顶点权重和恒为 1.0（和为 2.0 会让脸上刺出放射针）。
- 形变率判据必须带长度下限，否则 0.03 长的退化边会把正常表情报成撕裂。
- 每个表情能看出来：眨眼、嘴型位移要有量，不是只过结构校验。
- head 骨数与 torso 骨数必然不同（torso 多出专属骨），骨数不要照抄别的角色。

### 不要做

- 不要把形态键直接迁成 morph 动画。
- 不要在模型缺某个语义槽时手工往产物里补骨；缺槽替代在 `SLOT_FALLBACK` 合同里改，改完只需重烘表情并重新嫁接 head。
- 不要在本步之后再改 mesh 而不重跑 S4：骨数变了，旧 `.anim` 会让 entity 整体不渲染。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：各表情重建率、缺槽与替代清单、head/torso 骨数。
