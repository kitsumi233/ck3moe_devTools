# TASK S4：动画重定向、head 嫁接、state 注册

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_2_animation.md`。

## 提示词

你要给角色 `<ck>` 生成完整的身体动画（约 345 条 torso）和头部动画（约 357 条 head），并把完整 state 表写进 `.asset`。清单以原版 CK3 的 state 表为准，不要手写。

### 先读

- `PIPELINE.md` 的 S4 一节和“硬顺序”。
- `LESSONS_2` 全文。尤其是：rest 是第 0 帧不是 bind pose、`body_root` 旋转必须一起传、左右镜像骨轴、head/torso 同名配对、循环与接缝。
- 先确认 `X.bake_dir('<ck>')`（在 Python 里 `import moe_chars as X`）：若落在 `release_revisions` 路径下，这是已发布角色，必须写暂存目录，不能直接生成。

### 步骤

1. 批量入口（推荐）：`python tools/build/import_chars.py --ck <ck> --only torso,head`。产物存在即跳过，`--force` 重做。日志在 `work/logs/import_<ck>_*.log`。
2. 手动等价命令（换角色必须显式传目录、mesh、前缀）：
   ```
   python tools/anim/build_all_anims.py --part torso --out-dir work/<ck>_anim_bake --rest-mesh work/<ck>_anim_bake/moe_torso.mesh --out-prefix moe_
   python tools/anim/graft_all_head_anims.py --out-dir work/<ck>_anim_bake --head-mesh work/<ck>_anim_bake/moe_head.mesh --out-prefix moe_
   ```
3. 注册 state 表发生在 S6 的 `build_all.py` 里，且必须在 `build_moe_mod.py` 之后；单独验证用 `python tools/anim/register_all_anims.py --dry-run ...`，参数见 `PIPELINE.md`。
4. 单条调试：`tools/anim/retarget_vanilla_anim.py`（只能做 torso）、`tools/anim/graft_spine_into_head_anim.py`（清单只有 17 条情绪动画，不能用于完整集）。

### 验收（逐项给出实测值）

- `python tools/verify/verify_all_anims.py --seams`：引用完整、骨数一致、head/torso 脊椎 rest 一致、循环接缝。
- `python tools/verify/check_head_torso_sync.py --char <ck>`：头身脱节量。
- `python tools/verify/verify_moe_anim.py`、`check_anim_loop_seam.py`、`verify_retarget_root.py`、`check_shared_bone_pose.py`。
- 非循环动画的首尾差是真实末端姿态，不要把它压成 0。循环动画首尾差应接近 0。
- 骨数：每条 `.anim` 的骨数严格等于所属 mesh 的骨数。

### 不要做

- 不要共用暂存目录给两个角色，同名 `.anim` 会互相覆盖。
- 不要把别的角色的动画直接复制过来，除非骨数完全相同并经过 `LESSONS_2` 里的跨角色复用条件核对。
- 不要在 torso 重定向之后跳过 head 重新嫁接。
- 不要用 idle 判断物理或手臂是否正常，先看动作本身躯干有没有动。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：torso/head 条数、骨数、`verify_all_anims` 的汇总行、接缝超标的动画清单。
