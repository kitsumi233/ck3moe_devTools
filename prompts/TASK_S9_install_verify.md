# TASK S9：装机与实机验收

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_5_build_verify_ops.md` 的“验收与日志判读”“长任务与进程管理”两节。

## 提示词

你要把已通过自检的产物装进隔离 userdir，启动 CK3，并给出可复核的验收证据。游戏完整加载约 12 分钟，浪费一次很贵，所以装机前先把能离线验的都验完。

### 先读

- `PIPELINE.md` 的 S9 一节。
- `LESSONS_5` 的日志判读条目：旧日志的 0 错误不能当修复成功、缓存损坏与 shader 语法错的报错一模一样、截图工具会静默抓错窗口。

### 离线检查（装机前）

1. `python tools/verify/final_verify.py --prefix moe_<ck>_`：骨数、权重和、head 里的躯干骨、头身帧数、共享骨差异、头发通道、循环接缝。
2. `python tools/verify/verify_asset.py --asset <.asset 路径>`：state 引用完整、骨数严格相等。
3. `python tools/verify/check_meshsettings_coverage.py`、`check_head_torso_sync.py`、`verify_all_anims.py --seams`。
4. 可选包的离线契约：`verify/verify_wardrobe.py`、`verify_cosplay_rules.py`、`verify_moe_age_types.py`。

### 装机

1. 装机会写隔离 userdir，默认把物理优先的最终动画物化进模型包。`--no-physics` 是“从基础烘焙输入恢复无物理对照”，不是禁用某个包；是否传它要先问用户，不要自己决定。
2. `python tools/build/finalize_mods.py [--no-physics]`。自检不过它会拒绝装机，不要绕过。它会先改写输出根（替换模型包里的动画文件、写 descriptor 与缩略图），所以只能对沙盒或可丢弃的拷贝运行（`MOE_OUT_ROOT=<沙盒目录>`）；对有人手改过的 `mod/` 运行前必须先得到所有者同意并备份。
3. 可选包用 `python tools/build/install_optional_mod.py`：默认只装不启用，`--enable` 要显式，`--restore` 能还原加载顺序。

### 启动与日志

1. 先确认没有旧的 CK3 进程；不要强杀，强杀会留下截断的 shader 缓存。
2. `powershell -ExecutionPolicy Bypass -File tools\build\launch_ck3.ps1`（默认清旧日志，`-KeepLogs` 保留）。
3. 等日志停止增长再查，进度看 CPU 与内存是否还在涨。
4. 查日志用 `grep -a`，四项必须全为 0：`Failed to create material`、`PlayAnimation failed`、`Unknown animation state`、`Missing default_state`。
5. 判断 mod 是否挂载看 `debug.log` 的 `Mounted Data:.*moe_` 与 `Folder not found`，不要看 `Enabled`。
6. 记下进程启动时间，确认晚于最后一次改动。

### 不要做

- 不要把构建、装机、启动写成一行。
- 不要截图而不确认前台窗口是游戏；不要用窗口标题判断加载完成。
- 不要用渲图当验收，骨数不匹配、缺 meshsettings、缺 `portrait_modifier` 在渲图里完全正常。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：进程启动时间、四项日志计数、挂载的 mod 列表、离线检查输出摘要、没验证到的部分。
