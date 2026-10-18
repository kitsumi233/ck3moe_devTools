# LESSONS 3：CK3 引擎侧与 mod 文本侧的反直觉行为

本文覆盖「游戏里到底生效没有」这段流水线：portrait group/DNA、accessory 与基因、事件、GUI 与换装、1.20 载体、shader 卡通化、布光描边、mod 加载与覆盖。遇到「零报错但不生效」，按症状找小节，用条目里的判据（日志关键字、grep 引擎文件、像素统计）定位，别猜一处改一处。记忆是时间点观察（距今约 1~9 周），以实机和当前代码为准；标「未实机验证」的只作线索。

## 一、portrait group、ethnicity 与 DNA

**书签预览正常、进游戏变回原版：预览不走 ethnicity 校验**
现象：真实开局立绘变回原版人类。真因：文化 `ethnicities` 指向 human group 的条目，与角色 DNA 的 group 不一致，引擎静默重随机 DNA。判据：存档 `dna=` 解码 416 字节（104 gene）即已被重写；修法是 `common/ethnicities` 顶层写 `portrait_group` 建自家条目。验证 group 生效要把 entity 换成原版男童，换成原版女性与没生效外观相同；旧存档已固化，须开新档。
来源记忆：ck3-moe-ethnicity-group-bug, ck3-moe-portrait-group-verified

**一个角色一个 group：同 group 内同 sex+age 的 type 永远只用第一个**
现象：后加角色进游戏全是第一个角色，书签预览却各自正确。真因：type 按 sex+age 实时重挑（`male/female/boy/girl` 是互斥槽位），DNA 里的 type 只用于定位 group，type 级无 trigger。判据：每 group 只装一个角色；要「忽略性别」则放 sex×age 四个互斥 type，道具基因与 animations.txt 别名也覆盖四个。
来源记忆：ck3-portrait-type-is-recomputed-so-one-char-one-group, cosplay-culture-gate-moved-to-units-pack

**指派进 group 只有 set_ethnicity，跨 group 必重随机 DNA**
现象：文化声明表达不出条件指派。真因：文化 `ethnicities` 是纯加权列表（242/242 个原版块零 trigger），没有 `set_dna`。判据：用 `debug_log` 打点后 grep debug.log（没报错不等于跑了）；保颜色要给 color gene 写零宽范围（min==max），不写 `template`，否则报 `Failed to find gene X in portrait type Y`。
来源记忆：ck3-set-ethnicity-works-history-effect-is-the-reliable-path

**history 的 dated effect 不对开局前已死的角色执行**
现象：20 个「生成即死亡」预览角色全是灰色原版人。真因：dated effect 跳过死者，灰色来自 `portrait_base` 对死者强制 `grayscale`。判据：debug.log 里 4 个活人有 `MOE_HISTORY`、死者 0 条；占位角色要活到开局，在 `on_game_start_after_lobby` 事件里先 `set_ethnicity` 再 `death`，GUI 侧 `blockoverride "grayscale" {{}}`。
来源记忆：ck3-history-dated-effect-skips-dead-chars

## 二、accessory、基因与 modifier

**衣服不渲染且零报错：accessory 要三层，且每个块自报 portrait_group**
现象：改三处让日志归零，衣服仍不出现。真因：缺第 3 层（定义、gene、`portrait_modifiers` 里的 `dna_modifiers.accessory`「实际穿上」）；另外归属不从基因推断，缺省即 human，会报 678 条 `only scripted to work in portrait group human not moe_flo_g`，像权限问题。判据：把 `torso` 临时指向衣服 entity，衣服完整出现即断在 accessory 通道；`portrait_group` 须与 `entity` 平级，写在文件顶层会让后面每条报 `Unexpected token`（648 条）；DNA 模板名要加引号、隐性等位给 0，写 `255 255` 不报错但无效。
来源记忆：ck3-accessory-needs-three-layers

**基因名与 modifier 容器名是全局的，复用原版名被 human 吃掉**
现象：同名后原版条目刷 `Unknown portrait modifier`，或我们的道具静默跳过。真因：两类名字全局按名合并，不按 group 分槽。判据：故意同名冲突一次，原版条目开始报 `Unknown`（原版 236 条全报）即全局；修法是自造基因名与容器名并带 `portrait_group`。五层链缺一层即静默：animations.txt → 容器 → gene+template → 基因模板 → accessory。
来源记忆：ck3-gene-and-modifier-names-are-global（取代 ck3-props-need-same-named-gene-in-own-group 的同名复用方案）

**原版模板逐 type 穷举无兜底；挂点骨缺失却会刷屏**
现象：「翻书只有动作没有书」。真因：186 个原版模板无一有 `default`，自定义 type 查不到条目；不存在的 attach 节点引擎不静默（`.anim` 里不存在的骨才静默丢弃）。判据：模板里用本 group 的 type 名（写 `male` 报 `Type male is not part of the <group> group`）；grep error.log 的 `has no attach point named`，改挂实有的腕骨。
来源记忆：ck3-props-need-same-named-gene-in-own-group

**animations.txt 的 portrait_modifier 块与 pack 行引擎只取一份，追加全丢**
现象：有动作无道具，文件三层都齐。真因：原版 186 个变体每个只有一块、154 个每个只有一行 pack，旁边追加的被忽略。判据：注入前先数原版同关键字的出现次数，分布是 `{1: N}` 就必须合并进已有那份；验收 `chancellor` 块 `grep -c 'portrait_modifier = {'` 为 1 且含我们的键。「早先别的配置下见过效果」不算验证。
来源记忆：ck3-portrait-modifier-one-block-per-variant

## 三、事件与模型池

**on_action 只能用 events 追加，effect 是单值会顶掉原版**
现象：原版 `on_game_start_after_lobby` 被顶掉，连带 21 条 `Event X is orphaned`。真因：`effect` 单值只取最后加载的，`events` 是列表可跨 mod 追加。判据：error.log 的 `jomini_onaction.cpp: There is more than one 'effect' defined`；自己的标记触发了只证明我们的块赢了。该 on_action 还是 scopeless，挂不了 `character_event`。
来源记忆：ck3-set-ethnicity-works-history-effect-is-the-reliable-path

**模型池：空 on_action 分发、子 mod 自注册、水塘抽样；trigger_event 是入队**
现象：分发后接「清计数器」「无人认领则退回原版」，判据永远为假。真因：`trigger_event` 入队，后续语句先于子 mod 事件跑完；`set_ethnicity` 只吃字面量键，只能让持模型的子 mod 自己写。判据：第 k 个候选以 `100/k` 认领，实机 `MOE_ASSIGN` 三套分别 8/5/3 次；`chance` 是百分数。
来源记忆：ck3-model-pool-via-onaction-reservoir

**带 days=N 的延迟事件在开局暂停时永不触发**
现象：欢迎弹窗从未出现，日志却有 dispatch 标记。真因：延迟事件挂游戏日历，暂停不走。判据：日志无日期推进记录（本例 43 分钟无）；开局说明类弹窗用无延迟的 `trigger_event = X`。
来源记忆：ck3-delayed-events-need-unpaused-time

## 四、GUI 与换装

**自造顶层 window 引擎从不创建，visible 永不求值，零报错**
现象：点交互无反应，error.log 零报错，gui_warnings.log 是空文件。真因：顶层 widget 由 C++ 按名字查出来创建。判据：把窗口名 grep 进 `ck3.exe`，原版名各 1~3 次、自造名 0 次，就不必再查脚本；修法是作为子 widget 注入已实例化的容器（`gui/hud_sidebars.gui`），子窗口自写 `layer = windows_layer`。
来源记忆：ck3-mod-toplevel-window-never-instantiated

**portrait_button 不带 mask 整张乘 0，实时肖像一像素都不出**
现象：左栏仍是缩略图，中栏像素统计 0% 内容，日志无一字。真因：`gui_portrait.shader` 把肖像和底图都乘 mask 的 `.a`，没绑 mask 采样为 0；原版 12 种类型无一不带。判据：看像素统计不看日志；datacontext 是 Character 时直接 `using = portrait_base`。
来源记忆：ck3-portrait-button-needs-mask

**横向 flowcontainer 加 max_width 不折行；左下 3D 立绘不吃 layer**
现象：20 格排成一行；layer 从 3 改到 8 仍被立绘遮住。真因：原版 1231 处 `max_width` 全在 text 件上；3D 立绘不走普通层级。判据：用属性前先 grep 原版的使用上下文；网格用每 5 格一个 hbox 加 `ignoreinvisible = yes`；遮挡用原版变量 `hide_bottom_left_HUD`（开窗 Set、关窗 Clear）。
来源记忆：ck3-gui-flowcontainer-no-wrap-and-3d-portrait-ignores-layer

**大厅期没有 GetPlayer，变量要挂在选中角色身上**
现象：在选规则页加入口，读不到玩家变量。真因：原版大厅 gui 里 `GetPlayer` 与 ScriptedGui 都是 0 次，槽位变量要到 `on_game_start_after_lobby` 才写。判据：变量挂选中角色，另开空 on_action 在大厅期登记；验收看 debug.log 的 `MOE_COS lobby_open` → `MOE_SLOT register` → `MOE_SLOT claim`。未实机验证。
来源记忆：ck3-lobby-has-no-getplayer-scope-vars-on-character

**换装格显示专用占位角色而非本尊，外观状态有两份**
现象：切皮肤后选项格没跟着变。真因：格子 datacontext 是开局即死的占位角色（故意不指向本尊，防衰老死亡污染预览）。判据：切换事件里同时对 `character:<slot_char_id>` 施同一效果。动态页签方案补建预览角色须封顶 3 次防反复补建，该方案未实机验证。
来源记忆：cosplay-slot-preview-is-separate-char, wardrobe-pack-tabs-dynamic-lists

## 五、1.20 载体

**1.20 按骨骼下标缓存，骨序与原版不同会照到裙子，严重时越界崩溃**
现象：相机照到裙子；改骨名、重排骨序、改挂 `body_root`、per-type 相机键（`Unknown object key`）都失败。真因：相机节点、`shared_pose_entity`、跨 entity 查骨按下标缓存，我们的骨序与原版 134 骨不同。做法：torso 载体=真骨表+退化三角形（走流式，不能 Never），head 载体=自有 5 骨 rig，真头真身做 accessory 并 `shared_pose_entity = torso`；face/outfit 不加 `portrait_decal`（否则下巴出阴影），Eyes+ 层贴 eyeblend（否则没高光）。head 设 Never 与 10-01「Never 全部关掉」定案有张力，以实机为准。
来源记忆：ck3-120-carrier-architecture

**1.20 原版立绘全空：别从报错数量推根因，按 mod 逐个关掉定位**
现象：开某模型包后原版角色全不显示，纯原版和仅 shader+core 正常。真因：两个误判已排除，一是据 `no animated mesh`（`pdx_entity.cpp:477`，能填满 10 万行上限）给 head/torso 加 `streaming = Never`，反而全空；二是颜色基因改名/删除，两次失败。判据：封顶日志里排第一的报错常只是症状；当时嫌疑是 customization rule 缺 `accessory_color_override_block_0~3`，待实机验证。
来源记忆：ck3-120-streamed-mesh-no-animated-mesh-error

## 六、shader 与卡通化

**双面不止 CullMode=none：背面法线不翻转，内侧在卡通下近黑像透明**
现象：裙摆、袖口内侧「变透明」，而 6 个 Effect 已是 `CullMode = none`、贴图 alpha 每级 mip 全 255。真因：原版 `DOUBLE_SIDED_ENABLED` 分支没有任何 Effect 定义过，背面法线朝外，N·L 为负被压成暗档。判据：查 `Normal * (PDX_IsFrontFace ? 1 : -1)` 到位，别再查剔除和 alpha；shader 只能整份复制，Effect 名须整行精确匹配，否则 `portrait_skin` 先命中 `portrait_skin_face`。
来源记忆：ck3-double-sided-needs-shader-copy

**卡通化量化光照强度而非 Color，两档必须是常量**
现象：脸无硬边、直方图单峰、阈值 0.18→0.08 几乎无变化。真因：一个 bug 三个症状，`lerp(ToonDiffLight*0.88, *1.10, ToonEdge)` 两档仍乘连续光照；正确是 `ToonHue = ToonDiffLight / max(ToonKey, 0.0001)`，两档取常量只留色相。判据：自检全绿不等于对，要拿渲染采样对比 diffuse 贴图，脸部直方图必须双峰；自检做反向验证（改回旧写法必须退出码 1）；「调参数没反应」先怀疑逻辑没生效。
来源记忆：toon-must-quantize-lighting-not-color

**平涂要把 ToonEdge 与 ToonGain 一起钉死，出口是连乘就逐项过一遍**
现象：钉了 `ToonEdge = 1.0` 脸上仍有明暗。真因：亮档是 `ToonHue * LIT_LEVEL * ToonGain`，`ToonGain = clamp(sqrt(ToonKey/GAIN_REF), 0.70, 2.20)` 随逐像素法线变，仍有 3.1 倍跨度。判据：两项同钉（代价是皮肤不跟随场景光）；别用「阈值设 0」平涂（`smoothstep(0,0,x)` 除零）；要跟随场景光就另累加不含法线项的标量 `SceneLum`，court 侧拿不到则宏回退 `ToonGain = 1.0`，产物按 portrait 与 court 两侧分别断言。
来源记忆：toon-flat-must-pin-gain-too

**二值化静默失效三因：补光 ≥ 阈值、脸贴图偏暗、几何法线（宏全在、自检全绿）**
现象：部分角色脸没有二值化，或鼻侧一块深褐硬边。真因：补光进了 `ToonKey`，≥ `MOE_TOON_THRESHOLD`（0.18）等于每个像素被垫进亮档（脸补光 0.26，补光 0.14 的腿分档清楚）；脸 diffuse 亮度中位在 30 个角色间 134.9~189.4，越暗越易全进亮档；交界线落点由几何法线决定。判据：脸部亮度两峰只差 14 级（168/182）是渐变噪声；构建自检硬闸「补光 ≥ 阈值退出码 1」，阈值不低于约 0.15；调亮动 `LIT_LEVEL`/`GAIN_MAX` 不靠提补光；定性时把 `MOE_TOON_SHADOW_TINT` 临时改纯品红（SAT 设 1.0）看色块位置，验收看交界线位置不看暗部面积比。
来源记忆：face-fill-above-toon-threshold-kills-banding, toon-shadow-band-position-depends-on-geometry

**卡通效果要覆盖同一张脸的全部皮肤槽位，否则一块皮肤两套光照**
现象：眼白、瞳孔比脸暗，大腿丝袜与脸色差大。真因：`TOON_EFFECTS` 漏了 `portrait_skin`，眼白按材质合同走它（改走 `portrait_eye` 会被 palette 乘粉红：贴图 R−B +29.5，渲染 +63.0）。判据：列出该角色 asset 里每个槽位的 `shader =` 与 `TOON_EFFECTS` 求交集，别只看单张贴图数值。用户定案：衣服头发走原版光照，只卡通化皮肤与眼睛。
来源记忆：toon-effects-must-cover-all-skin-slots, shader-direction-cloth-vanilla-skin-toon

**拿实现字面量当 replace 锚点或自检判据，换实现时静默失配**
现象：试第二种明暗风格后王座厅半透项整段丢失，构建全绿。真因：锚点是 `"Color = ToonDiffOut + ToonSpec;"`，出口改了就零命中，且 court 是独立副本，portrait 侧自检覆盖不到。判据：用正则按契约定位并断言命中数恰为 1；`grep -c` 对比两份副本；照抄参考参数要核输入值域（half-Lambert 恒在 [0.5,1.0]，ramp 位置最大 0.60，前三段永不命中）；复刻色彩管理管线先测灰。该风格实验已撤回。
来源记忆：shader-style-literals-are-not-anchors

**SSAO 是全屏后处理，会把硬边糊回去，且有第六个写入点走另一条分支**
现象：脸上眼窝、鼻侧有浑浊斑块，「脸卡通、衣服糊」。真因：`ssao_apply.shader` 乘法混合在像素着色器之后，强度来自 `Out.SSAOColor` 的 alpha；写入点是调色板乘法 5 处加 `PS_attachment` 里硬编码 alpha=1.0 的 1 处。判据：只改前 5 处会漏衣服与挂件（`.asset` 里 16 处引用），生成脚本硬断言命中数 ≥6；降权不要设 0，否则头发与脸、衣领与脖子糊成一片。
来源记忆：ck3-toon-ssao-is-fullscreen-postprocess

## 七、布光与描边

**脸上的暖红来自原版布光配置，覆盖 environment_standard 一个块即可**
现象：脸上一层红「遮罩」。真因：原版主光 `hsv{ 0.1 0.45 1.5 }`，cubemap 实测 R−B +27.2。判据：全脸 R−B 一致为正是光源带色，局部斑块是 SSAO，前发投下的灰绿脏块是 shadow map（光源设 `affected_by_shadow = no`）；脸 diffuse 实采 (242,188,183) 排除贴图。覆盖文件加 `zz_` 前缀、绝不与原版同名（同名会丢 200 多个 environment）、带 BOM；缺 `type` 报 `portraitenvironment.cpp:50 Light is missing a type!` 且整个 environment 静默退回原版。代价：原版 NPC 一起变。
来源记忆：ck3-portrait-lighting-is-config-not-shader

**改布光要直接给方向不要搬位置，光源槽位写死 3 盏**
现象：搬光源位置失败三次，角色只剩环境光。真因：`look_at_node` 指向躯干节点而非世界原点（实测 `|P0|` 305.3 对水平距离 160，多算 145.3）；光源数组是 `[3]` 且布局由 C++ 填充，改 `[4]` 会推移 `DecalCount`。判据：「只剩环境光」表现为没有明暗交界线，不是变黑；shader 里直接构造 `_ToLightDir`；跟随相机补光会在下颌线切出色差（脸 142.5、脖子 104.1）。底光已于 2026-09-07 被用户撤销：为掩盖其他缺陷调出的参数，缺陷修好后要主动撤。
来源记忆：ck3-portrait-light-give-direction-not-position

**描边分两层，墨线阈值要随 fwidth 抬高；遮挡边只有反向外壳画得出**
现象：细圆柱（腿、手指、发束）墨线不到 1 像素被 MSAA 吃掉；腿在尾巴前这类遮挡边根本没线。真因：固定 `abs(N·V) < 0.22` 是角度带宽，屏幕宽度正比曲率半径；遮挡边是深度不连续，N·V 与 GUI 色度差层（拿不到深度法线）都画不出。判据：阈值=基线+`MOE_INK_PX * fwidth(N·V)`（1.5 像素），墨线放卡通化之后；遮挡边要反向外壳（原版 `portrait_hair_backside` 是 .mesh 里独立的第二个 Shape），需重导出 mesh，记忆里只有方案、未见落地。
来源记忆：portrait-inner-lines-two-layers, ck3-occlusion-outline-needs-inverted-hull-mesh

## 八、mod 加载与覆盖

**同相对路径的文件后加载者整份顶掉前者，产物归属要在写文件时声明**
现象：某包 mesh、700 条 `.anim`、portrait type 全在，`verify_all_anims` 全绿，实机完全不渲染。真因：VFS 按相对路径覆盖，两个子 mod 的 `.asset` 同名，先加载的从未被读；事后正则分流还掩盖了另外两个零日志问题（共用贴图按名字猜归包、单启用一个包缺自己的 `.asset`）。判据：日志只有 `portrait_type.cpp:80 Undefined entity 'moe_head_entity' found for ... portrait part head/torso` 两行，夹在 894 条 accessory 报错里，且只有一侧报错；自检用「相对路径跨包唯一」「每包各有 `.asset`」「core 无模型资产」，别绕过；同名文件靠 load order 覆盖，`dependencies` 顺序是功能性的。
来源记忆：ck3-same-path-asset-silently-overridden, moe-out-owns-product-placement

**同路径整文件覆盖让核心里的改动在扩展包存在时静默失效**
现象：装了解限扩展包后，核心里改的换装逻辑不生效。真因：扩展包同路径整文件覆盖核心 3 个文件（交互、`41_moe_portrait_rules.txt`、`40_moe_cosplay.txt` trigger）；文化门同理，核心恒真、文化包同路径覆盖，同 key 跨文件不可靠。判据：改这 3 个文件须同步核对副本（目前逐字节相同）；测试环境必须带扩展包，只带核心时普通角色没有换装按钮是规则不是 bug。
来源记忆：unlocked-overrides-three-core-files, cosplay-culture-gate-moved-to-units-pack

**基础包不得碰地图数据：书签、持地、王朝、纹章都要关**
现象：基础组合与其他 mod 兼容性差。真因：`history/titles`、`common/bookmarks` 会改地图数据，用户 2026-10-01 定案兼容性优先于开局便利。判据：`build_moe_mod.py` 的 `PLAYABLE_STARTS = False` 下，基础组合不得出现 history 可玩角色、landed_titles、bookmarks、map_data；开局即死的槽位占位角色保留（换装预览要用），新入口走换装窗口或可选包。
来源记忆：no-bookmark-starts-for-compat
