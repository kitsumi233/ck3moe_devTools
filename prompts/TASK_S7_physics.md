# TASK S7：物理烘焙（头发、裙子、披风等挂件）

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_4_physics.md`。

## 提示词

你要给角色 `<ck>` 把 PMX 里的刚体烘成 `.anim` 里的物理骨通道。解算器是原生 C++（Bullet 为主，另有 PhysX 路线），Python 侧读 PMX 和基础 `.anim`，写回物理版。先单条，再决定是否全量。

### 先读

- `PIPELINE.md` 的 S7 一节和硬顺序第 7、8 条。
- `LESSONS_4` 全文，尤其是“别再试的方向”：避免重走已证伪的路。
- 动手前确认：有没有别的烘焙、预览服务在运行（本机 8765 端口可能有人在用预览服务）。只读、不停止它们，也不要在同一目录同时写。

### 步骤

1. 编译解算器（一次性，需要 MSVC、CMake、Ninja 和 `third_party/bullet3`）：`powershell -ExecutionPolicy Bypass -File tools\physics\moe_bullet\build.ps1`。
2. 同步基础动画输入：`python tools/build/sync_base_animation_inputs.py --characters <ck> --jobs 8`。解算读这个目录，不读已含物理的模型包动画。
3. 单条：`python tools/physics/moe_bullet_bake.py --ck <ck> --anim stayback_start --out-dir work/bullet_native/<ck> --debug-top 8`。
4. 核对日志四行：`[bake]` 的循环判定、各组动态摆幅、分离率拉长峰、`[moe_bullet]` 的运动学/动态刚体数。
5. 满意后再全量，输出到隔离候选：`python tools/physics/moe_bullet_bake.py --ck <ck> --all --jobs 12 --out-dir work/physics_candidates/<ck>`。
6. 批量十动作试验：`python tools/physics/trial_response_batch.py --root <试验根> --characters <ck>`；评估抖动用 `physics/ensemble_hf.py`、`band_gate.py`、`ensemble_compare.py`。
7. 预览：`python tools/physics/open_physics_debug.py --read-only --open`。本地服务，只绑回环地址，无账号体系；不要暴露到网络。

### 验收（逐项给出实测值）

- 对每个角色至少用躯干会动的动画测试；idle 测出 0.000° 不说明物理坏了。
- 评估抖动用副本系综，判差异用均值±2σ，不要拿单次运行比较。
- `python tools/verify/verify_phys_kick.py <ck> [anims_dir] --glob "*idle*"`：链级踢击指纹，且要对照无物理的基础动画剔骨。
- `physics/passthrough_check.py`：裙子是否穿到腿墙另一侧。
- 候选必须通过完整配对验收后，才能显式纳入发布输入；日常装机不会自动接纳新结果。

### 不要做

- 不要在烘焙期间运行任何会重写模型包 `.asset` 的构建，计划会变空但返回 0。
- 不要自己停止别人的烘焙或预览；内存紧张只报数字和建议。
- 不要在批次进行中改进了代码指纹的文件（如 `moe_bullet_bake.py`、`bone_twist.py`），`--resume` 会让整批判失效；失败者另建新根补烘。
- 不要从 Git Bash 里用 `$GROUPS` 做变量名，它是只读特殊变量。
- 不要重复已证伪的杠杆（见 `LESSONS_4` 第九节）。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：后端与参数、每个动作的摆幅/分离率/踢击指纹、失败动作清单与日志位置。
