# TASK S6：生成 mod 文本并部署（只在沙盒里）

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_3_ck3_mod_gui_shader.md` 与 `LESSONS_5_build_verify_ops.md`。

## 提示词

你要用生成器为新增或变更的角色产出 mod 文件。注意当前约定：`mod/` 是以创意工坊版为底、可能被手改过的目录，**不要对它跑生成器**。所有生成都写到沙盒目录，再 diff，只把需要的条目手工并回去。

### 先读

- `PIPELINE.md` 的“硬顺序”和 S6 一节。
- `LESSONS_5` 的“构建顺序与装机链”和末尾“当前状态提醒”。
- `LESSONS_3` 的“accessory、基因与 modifier”“mod 加载与覆盖”。
- `tools/build_all.py` 顶部注释：为什么顺序必须固化。

### 步骤

1. 造沙盒：把当前 `mod/` 拷到一个空目录（体积可能是几十 GB，先检查剩余空间，再取得所有者同意）。
2. 设输出根：`MOE_OUT_ROOT=<沙盒目录>`。该变量由 `tools/lib/moe_out.py` 读取，之后所有生成器都写到那里。
3. 先只跑到不装机：`python tools/build_all.py --no-sync`（最后一步 finalize 仍会写沙盒：物化动画、写 descriptor）。要从某一步重来，用 `--from <步骤名>`。
4. 失败就停，读失败那步的输出，找真因。不要绕过自检。
5. 用目录比对工具把沙盒产物与 `mod/` 对比，只挑与本次角色相关的条目，手工并入 `mod/`，保持 BOM 与 CRLF。
6. 并入后再做一次针对 `mod/` 的只读检查（见 S9）。

### 验收（逐项给出实测值）

- `build_all.py` 的“产物自检 ✓”：关键产物（shader、放大相机、换装窗口、布光、各角色道具、基因）都在，`.asset` 的 state 数大于 500。
- 日志没有 `Unexpected token`。出现时先查：中文注释、`]]` 提前闭合 PDX 块、BOM 读写不对称。
- 基因名与 modifier 容器名没有复用原版名。
- 手持道具的 accessory 三层齐全（定义、基因、`portrait_modifier`）。
- 一个角色只属于一个 portrait group，ethnicity 与 group 一致。

### 不要做

- 不要对 `mod/` 直接跑 `build_moe_mod.py`、`build_all.py` 全流程、`finalize_mods.py`、`publish_mods.py`。
- 不要把“构建 + 装机 + 启动”串成一行。
- 不要手工单跑 `build_moe_mod.py` 后忘了补 shader、gui、描边、布光、道具、换装 gui、register。
- 不要新增顶层 gui window，引擎不会实例化。
- 不要加书签开局、持地、王朝这类会碰地图数据的内容。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：沙盒路径、每一步耗时、自检输出、并回 `mod/` 的文件清单与差异统计。
