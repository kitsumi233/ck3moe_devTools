# TASK S8：CK3 1.20 载体架构转换（只对新包）

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_3_ck3_mod_gui_shader.md` 第五节“1.20 载体”与 `LESSONS_2_animation.md` 第七节“相机锚点”。

## 提示词

CK3 1.20 按骨骼下标缓存相机节点和共享姿态，我们的骨表顺序与原版不同，所以真 mesh 要做成配件，由一个只有退化三角形的骨架载体带动画。你要把新模型包 `<包目录>` 转成这个架构。**只对新包做，显式传包名，不碰已发布的包。**

### 先读

- `PIPELINE.md` 的 S8 一节。
- `tools/build/carrier_pipeline.py` 顶部说明：八个子步骤的顺序。
- `LESSONS_3` 第五节，注意其中“流式加载与 Never”存在有张力的记录，以实机为准。

### 前置检查（先做，缺一项就停下报告）

1. 包目录来自沙盒（`MOE_OUT_ROOT`），不是已发布的 `mod/` 目录。
2. 在**工程根目录**运行，因为脚本里的备份路径 `work/bisect_...` 是相对当前目录的。
3. `tools/build/build_cam_rig.py` 存在。没有它，`carrier_pipeline.py` 前五步会原地改写包，第 6 步才失败，留下改了一半的包。缺失时不要运行。
4. 工程 `work/` 下有 `replay_pack_fixes.py` 和 `vnode_fix_all.py`、`vnode_postcheck.py`。
5. 包目录已备份。

### 步骤

1. `python tools/build/carrier_pipeline.py <包目录>`。
2. 然后（在工程 `work/` 下）：`python replay_pack_fixes.py <包目录>`，再 `vnode_fix_all.py <包名>`（需要在临时 cwd 里准备好备份，见脚本说明），最后 `vnode_postcheck.py`。顺序不能反：生成器产物缺 6 项后处理，不重放会断言失败。
3. 沙盒在另一个盘时，`prune_portrait_anims.py` 的同盘重命名会失败，要把 trash 放到同一个盘。

### 验收（逐项给出实测值）

- 脚本最后打印 `PIPELINE DONE <包名>`。
- `vnode_postcheck` 零异常。
- 每个 head pdxmesh 都指向 23 骨 rig；骨数严格相等。
- face/outfit entity 的 `game_data` 为 interval `0 1`，没有 `portrait_decal`（否则下巴出阴影）。
- Eyes+ 层贴回 eyeblend 图（shader 用 `moe_portrait_attachment`）。
- 进游戏后 `error.log` 无 `Failed to create material`、`PlayAnimation failed`，且没有越界崩溃。

### 不要做

- 不要给 torso 载体设 `streaming = Never`，会让原版立绘全空。
- 不要对已转换过的包重复运行转换脚本。
- 不要把相机节点改名或重排骨序来绕过下标缓存，已试过，全失败。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：包名、备份位置、每个子步骤的输出摘要、postcheck 结果。
