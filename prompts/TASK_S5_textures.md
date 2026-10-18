# TASK S5：贴图落位、properties/法线、alpha 与 DDS

用法：先贴 `00_START_HERE_for_AI.md`，再贴本文件，并附上 `LESSONS_1_model_expression_textures.md` 的“贴图与材质”“贴图落包与引用”两节。

## 提示词

你要给角色 `<ck>` 生成游戏能直接加载的贴图：diffuse 落位，properties 与法线按命名规则生成，半透明按 shader 槽位处理，最后转 DXT5 DDS。贴图脚本在 `build_moe_mod.py` 里按序被调用，这个任务是单独排查或单角色试跑，输出到临时目录。

### 先读

- `PIPELINE.md` 的 S5 一节。
- `LESSONS_1` 里关于 alpha、properties 通道、虹膜、眼神光层、贴图裸基名的条目。
- `tools/lib/moe_chars.py` 里该角色的 `tex`（正则）、`src`（源目录）、`tex_alias`、`tex_src`。

### 步骤

1. 只看效果、不落进 mod：`python tools/textures/build_char_textures.py --ck <ck> --out-dir work/<ck>_tex`。
2. 需要全透明层时：`python tools/textures/make_invisible_texture.py`。
3. 按 shader 槽拆共享 alpha：`python tools/textures/prepare_opacity_alpha_variants.py`。
4. 校验 alpha 语义：`python tools/verify/verify_texture_alpha_contract.py`。它读输出根下的 `.asset`，沙盒里要先设 `MOE_OUT_ROOT`。
5. 转 DDS：`python tools/textures/convert_textures_dds.py <目录...>`，可同时传多个目录。

### 验收（逐项给出实测值）

- 每张 diffuse 都有对应 DDS，且 DDS 时间戳晚于源 PNG。不要只看日志有几百行转换记录。
- `.asset` 里的贴图引用全是裸文件名（不带目录），且文件真的存在。
- alpha 判据从 shader 槽位读：皮肤与眼睛的 A 通道不能二值化；衣服 diffuse 二值化且每级 mip 都要重做。
- properties 贴图 A=粗糙度、G=高光强，常量贴图出不来高光；虹膜粗糙度不低于 0.32。
- 源贴图不是 png/dds（如 tga）时，确认没有落到错误的包根目录。

### 不要做

- 不要凭文件名关键词决定是否二值化。
- 不要对着单个样本总结通道语义，至少看三个模型多个部位。
- 不要用目测估的像素框测亮度，先画网格核对落点。

### 汇报

按 `00_START_HERE_for_AI.md` 的汇报格式，并附上：贴图数量、DDS 与源时间戳核对结果、alpha 契约自检输出、有疑问的材质清单。
