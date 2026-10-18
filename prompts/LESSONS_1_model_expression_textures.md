# 教训汇总 1：PMX 到 mesh、表情烘焙、贴图与材质

本文覆盖 PMX 导入后的头身拆分、材质归属、骨架骨数、表情烘焙、贴图与材质这段流水线，只收反直觉、会白花时间的结论。数字是对具体模型的实测，换模型会变，只当量级；动手前扫对应小节。"来源记忆"是作者本机记忆条目名，仅供溯源。

## 一、头身拆分与材质归属

### 头身分界按身高比例，材质名分不了头和身
现象：head 材质被集体报越界，或名字像头发的材质长在大腿上。真因：绝对 Y 被轴向（Blender 是 Z、.mesh 是 Y）和尺度（后期放大约 8 倍）同时坑，材质名是美术随手起的。判据：用占身高比例（脖子根 0.73，容差 0.055），拆件后逐材质打印世界 Y 范围，由人看渲图定归属，头发豁免下界。
来源记忆：moe-head-torso-must-split-clean

### 判归属只认骨骼父链
现象：袜环、飘带跟身体脱节，日志零报错。真因：head 动画不含躯干骨通道，head mesh 里躯干骨主导的顶点永远停在 rest（洛茜有 4274 个）。判据：统计顶点主导骨上溯父链，`min_verts=20` 滤噪，只查 head 方向，跑 `tools/verify/check_ownership.py <head.mesh>`；`tail_base` 听着像尾巴实为下身飘带，名字和高度判据都会失效。
来源记忆：head-mesh-must-not-hold-body-bones

### "身体没了"先查 clothes
现象：有头、手臂悬空、中间空洞。真因：torso 只有 1222 面，身体主体在 clothes 的 54646 面里，被衣服遮住的皮肤源模型里根本没建。判据：手臂悬空说明 torso 渲染成功；`python tools/lib/pdx_parse.py <mesh>` 看 submesh 面数，clothes 的 shader 为空串即未注册 meshsettings。
来源记忆：moe-torso-is-tiny-clothes-is-the-body

### 漂浮小点是被丢材质的残片
现象：立绘身后有 6 顶点小岛，离一切几何 16~21 单位。真因：披风主体材质（4162 面）被材质契约丢弃，同块布的少数面落在保留材质里成了孤岛。判据：先看孤岛蒙皮骨并查该角色被丢的材质，修在上游契约后重导入（孤岛 3→0），不删顶点。
来源记忆：floating-dots-are-dropped-material-fragments

### 缺一条 meshsettings 整层不渲染
现象：尾巴和大腿根整片不见，本地渲图正常，日志只有一行 `Failed to create material`。真因：meshsettings 只负责提供 shader_file，缺了引擎去默认 shader 里找 portrait_* 找不到；几何重划分后 torso 的 index 13 没补。判据：改拆分后跑 `tools/verify/check_meshsettings_coverage.py`，`.mesh` submesh 数必须等于 `.asset` 的 meshsettings 数；"不渲染"层也要注册，用全透明 diffuse 加会丢片元的 shader。
来源记忆：ck3-every-submesh-needs-meshsettings, ck3-alpha-to-coverage-kills-gradients

## 二、骨架与骨数

### MMD 控制骨零蒙皮，D 骨才带皮
现象：动画数值完美（膝角偏差 0.0°）腿却不动，骨架线框看不出。真因：`knee` 是 0 顶点控制骨，`legD` 带 3760 顶点，MMD 靠运行时让 D 骨跟随而 CK3 没有。判据：必须带蒙皮渲（`tools/render/render_anim_skinned.py`）；局部转角约 0° 的中间骨只继承，rest 朝向差 135°~178° 的影子骨才显式驱动；驱动串在链上的祖先骨（armTwist 是 elbow 的父）会让自穿插从 4869 涨到 5609。
来源记忆：mmd-control-vs-skin-bones

### 动画骨数必须严格等于 mesh 骨数
现象：进游戏只剩头，躯干和衣服不渲染，本地渲图全绿。真因：引擎按索引取骨，数量不等整段拒播（`Animation 189, Mesh 198`），本地渲染器按骨名取骨看不见。判据：按 entity 逐一比骨数相等，不放宽成子集，也别用"她实机正常"当理由；看 `.asset` 引用的那份而非目录里的遗留文件；日志搜 joints 不是 bone，缓冲写要等它停止增长再 grep（33KB 时 0 次，后来 11736 次）。
来源记忆：ck3-bone-count-mismatch-kills-entity

### 导出器把浮点噪声当动画
现象：只驱动 5 根骨的 idle，189 根里 159 根写了假通道，文件 26KB→477KB。真因：io_pdx_mesh 用 `set()` 精确比较，求逆误差在 1e-5 量级；恒定通道还会把骨钉在 rest 压平表情。判据：导出后通道数应恰好等于驱动骨数；补丁加容差 `ANIM_EPSILON = 1e-4`，插件升级会覆盖需重打；这噪声曾让"通道非空"校验假通过。
来源记忆：io-pdx-mesh-float-noise-patch

### CK3 动画只有骨骼通道，表情和动作分两条路
现象：想把 MMD 形态键直接迁成 morph 动画。真因：1995 个原版 .anim 只有 t/q/s 通道，全 gfx 搜 morph_animation 零命中。判据：表情走 shapekey 烘到代理骨；动作走骨骼，17/17 根可映射骨 rest 朝向差都超过 45°，必须逐骨 `R_corr = R_moe_rest⁻¹ · R_vanilla_rest` 校正；扭转骨、D 骨等带权重的附属骨要烘成显式关键帧。
来源记忆：moe-anim-migration-two-channels

### mesh 顶点是世界坐标，pdxmesh scale 会把头顶飞
现象：写 `scale = 1.35` 后头浮在身体上方、脖子断开。真因：我们的 head.mesh 是世界坐标（Y 83~144），原版是头骨局部空间，scale 把 attach 偏移一起乘了，头上移约 29.2 单位。判据：任何按比例缩放 mesh 的做法都有此问题，放大立绘只能走相机。
来源记忆：moe-mesh-is-world-space-so-scale-breaks-attach

## 三、表情烘焙

### 烘骨后每顶点权重和必须为 1
现象：眉心位移被放大，或脸上刺出放射状细针，日志零报错。真因：各槽独立写权重，眉心权重和达 3.942；归一化只数 fx 骨，漏了牙舌原生骨，洛茜 552 顶点权重和中位正好 2.0。判据：fx 与原生骨一起计入，超 1 整体等比缩到 1、余量归 head；验收直接量 `.mesh` 逐顶点权重和，异常数必须为 0，渲图器自己会归一化，画面不能当证据。
来源记忆：fx-morph-bake-needs-normalization

### 形变率判据在短边上永远爆表
现象：表情报出 53.8%~105.3% 撕裂，归一化后反而报 239%。真因：最坏边原长只有 0.0300，表情位移 0.5~1.6，分母趋零。判据：只取 `L0 >= 0.10` 的边，并同时报绝对分离量（最大 0.935，头高 60.5，渲图完整）；腿部同样的 352% 却是真撕裂，不能一概信或不信。
来源记忆：stretch-ratio-needs-length-floor

### 表情"淡"是烘焙层的两个缺陷
现象：用户报表情有点淡。真因：一根骨只有一个平移方向，逆向顶点被焊死在 head 上；单个双侧键被拆给 `_l`/`_r` 各一半权重。判据：用 `tools/verify/measure_expression_fidelity.py` 量重建率，正反双骨加双侧合并后 mouth_smile 24.7%→67.6%；旋转或缩放骨更差（46.2%、64.5%）；约束要从源 blend 量而不是烘焙产物，撕裂基线是真实 shapekey 而不是旧烘焙。
来源记忆：moe-expression-reverse-bones-and-bilateral-merge

### 每顶点 4 个影响超限被静默截断
现象：angry 唯一驱动眉毛的骨在产物里 0 顶点。真因：io_pdx_mesh 按权重留最大 4 个，只打一条没进构建检查的 warning；双侧单键没合并，眉骨权重被除以 2 先被丢。判据：head 占 1 格、fx 最多 3 格，截断先锁住位移最大的槽，截断后每槽必须仍有顶点；烘完看 bake_report 的 `shared_shapekeys` 与 `verts_after_cap`，share=2 的键必须进 `BILATERAL_MERGE`。
来源记忆：fx-cap-must-protect-semantic-slot

### 表情不明显是量级错不是逻辑错
现象：实机反馈表情不明显、没有眼球动画，而骨数、接缝、头身同步全 PASS。真因：眼球峰值位移 0.045（虹膜宽 1.8~2.2）且写进了深度轴（Blender 是 Z 向上）；同一语义槽跨模型幅度差 3~6 倍；169 条中性头零眨眼。判据：改表情先跑 `tools/verify/verify_expression_life.py`；同名 MMD 键跨模型语义可能不同（真面目在洛茜是 30 点眉毛键、在芙洛伦是 406 点眼部键），绑定表要核顶点数。
来源记忆：expression-visibility-needs-magnitude-check

## 四、贴图与材质

### 白遮罩真凶是 EyeShadow，关层要看同系列，定案只能进游戏 A/B
现象：上眼睑往下一片奶白遮罩盖住虹膜。真因：CK3 没有 additive，`alpha_to_coverage` 把 alpha 不低于 128 的部分硬切实心；元凶是 EyeShadow（index 10）不是 Eyes+，而 Eyes+ 在其他少前 2 角色里装着眼神光（虹膜亮像素 0%，芙洛伦 9.17%）。判据：改一层、重启、同视角截图；UV 包围盒统计、逐面采样、无光照的自研渲染器给过三个互相矛盾的元凶；关一层前先统计同系列多个模型的贴图。
来源记忆：ck3-alpha-to-coverage-kills-gradients

### 中间 alpha 在 CK3 是点阵筛子
现象：透过帽檐能看到大地图；另一次脸上有带色相的阴影。真因：只有 alpha-to-coverage 没有 alpha blend，帽檐有 2.4% 中间值，共用表情图集有 15.31%。判据：按 alpha 分组看 RGB（alpha 1..127 的 70,700 像素中位 192,116,102 即糊脸红褐）；衣服阈值 16，表情图集取中点 128；每级 mip 都要重做；不动 `_properties`/`_normal`；表情图集是腮红贴花载体，不能整层 no_render。
来源记忆：ck3-mid-alpha-becomes-see-through-sieve

### 二值化判据挂 shader 槽位，不挂文件名
现象：修完终末地表情图集，少前 2 的等价贴图 `extra.png` 仍没被二值化。真因：关键词表追不齐文件名；alpha 语义在槽位上：attachment、hair、face 的 A 是透明度，skin 与 eye 的 A 是 palette 染色强度，不能动。判据：`tools/verify/verify_texture_alpha_contract.py` 按 `.asset` 槽位对账，须在贴图转换前跑（转换会 unlink 源 PNG），审计 126 张抓出 5 张漏网；裸子串 `_da` 会命中角色名 c_daiyan。
来源记忆：texture-alpha-judgment-belongs-to-shader-slot

### 虹膜 alpha 是 palette 染色开关
现象：眼睛蒙一层橙红，色差极大的两张虹膜贴图渲出来几乎同色。真因：`PS_eye` 里 `ColorMaskStrength = Diffuse.a` 且是乘法，我们的虹膜 alpha 全是 255，原版 95.8% 是 0。判据：贴图 R−B 差 149、渲染只差 67.4 对 71.0；alpha 置 0 消橙红但虹膜变近黑（palette 兼着提亮），用户 A/B 后选保留染色；中间 alpha 走不通，混合区间来自引擎 per-instance 数据。
来源记忆：ck3-iris-palette-multiply-explains-same-colored-eyes

### properties 通道按 shader 读，不要凭名字猜
现象：眼睛只有均匀塑料光泽；另一角色的头发渲成黑色。真因：A=粗糙度（不是 R）、G=高光强、B=金属度，法线只读 G/A，眼睛 properties 是全常量；终末地 `_P` 图的 R 是二值遮罩（34.7% 为 0、65.3% 为 1）被当金属度写入，538 面头发漫反射趋零。判据：先统计各通道跨部位分布再用，实测 `_P` 的 B 才是粗糙度，金属度一律常量 0；properties 写未压缩 A8R8G8B8 的 DDS。
来源记忆：ck3-portrait-properties-channel-semantics；另见 docs/MODEL_IMPORT_PIPELINE.md「材质与贴图」

### 眼神光层与 X+ 叠加层会落进不透明槽
现象：部分鸣潮角色"只有眼白"，另一人衣服贴图异常。真因：眼神光层名五花八门（Eye_+、eyehi1、目+hi），旧关键词只认 `eyes+`/`eye+`，落进强制不透明的 `portrait_eye`，57~98% 透明区被画成实色；`bodyDown+` 这类叠加层落进 `portrait_skin`，alpha 被当染色开关。判据：新游戏导入时先枚举这两个槽里 alpha 低于 8 占比高的层；改后重建 asset 并重转 DDS，因为槽位决定 alpha 语义。
来源记忆：wuwa-eye-highlight-and-plus-layers

## 五、贴图落包与引用

### `.asset` 的贴图引用必须是裸基名
现象：画面正常，只是没高光、法线不对，error.log 的 162 条报错被当成基线噪声。真因：引擎对 gfx/models 建按基名索引的平表，带 `/` 的引用查不到；diffuse 走 `.mesh` 内嵌裸名所以正常，只有 meshsettings 覆盖的 specular/normal 静默失效。判据：asset 唯一贴图 ref 数等于日志唯一失败 ref 数（63 对 63）说明没有一条生效；`tools/verify/verify_asset.py` 要求带目录数、mod 内撞名、与原版撞名都为 0。`docs/MODEL_IMPORT_PIPELINE.md`「贴图落在哪」一节"相对 .mesh 目录解析"的说法已被实测推翻（已取代）。
来源记忆：ck3-texture-refs-must-be-bare-basenames

### 贴图落包与转换的两个静默失败，构建都 rc=0
现象：24 个角色的 Cheek 报 missing texture_diffuse；另一次终末地改了贴图逻辑始终不生效，转换日志 474 行看着正常。真因：源图是 `.tga` 时按原扩展名拷贝，路由只认 dds/png，落到第一个包根且不被转换；转换脚本 `main()` 只读 `sys.argv[1]`，第二个游戏包整包没转。判据：看 DDS 时间戳而不是转换记录；`find mod -iname '*.tga'` 应为 0；脚本用 `sys.argv[1:]` 全收，"扫到 0 个文件"当可疑信号，转换失败必须 `sys.exit`。
来源记忆：texture-source-non-png-misrouted, convert-textures-only-processed-first-game-package

### 皮肤别名贴图要改 `image.filepath` 的基名
现象：皮肤角色脸层 diffuse 变 None，或悄悄退回 256² 近黑的球面辅助图当 diffuse，零报错。真因：导出器写进 `.mesh` 的贴图名取自 `image.filepath` 而非 `image.name`；filepath 指向不存在的文件时图尺寸置 0 被选图函数过滤，源包改名而 PMX 没跟改同理。判据：先把源图复制成别名文件再改 name 与 filepath 并 reload；grep `.mesh` 二进制里的 `.png` 名，别只看 report；皮肤与本体同名骨 194~350 根但顺序全不同，不能复用本体动画。
来源记忆：skins-are-new-chars-with-tex-alias

## 六、判据与测量的陷阱

### 本地渲图不是引擎
现象：渲图全绿，游戏里整件 entity 不显示、脸上刺针、层不渲染、袜环脱节。真因：自研渲染器按骨名取骨、自己归一化权重、按材质名取图不走 CK3 shader、没有光照模型，单渲一件 mesh 时躯干骨也不动。判据：骨数、权重和、meshsettings、shader 槽位、光照这几类要直接量导出的 `.mesh`/`.asset`，或进游戏 grep 日志（`PlayAnimation failed`、`Missing default_state`、`Failed to create material`），并等日志停止增长再下结论。
来源记忆：ck3-bone-count-mismatch-kills-entity, fx-morph-bake-needs-normalization, head-mesh-must-not-hold-body-bones

### 目测的像素采样框会静默量到背景
现象：一天内两次报错，先是"脸 65、腿 150，脸太暗要提亮"（方向是反的），后是"眼区没有眼白"。真因：手估的框落在国王头顶、背景、头发上，假数据还能顺畅推出像样的因果解释，不会触发怀疑。判据：出数前先画 20px 坐标网格或把命中掩码叠回原图；优先程序化检测加宽 ROI（框歪会表现为命中像素数骤降）；卡通两档按 Otsu 拆成亮暗档分别报（重标后脸亮档 157.5、腿亮档 152.2，脸并不暗）。
来源记忆：pixel-sampling-boxes-must-be-grid-verified
