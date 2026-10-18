# 物理烘焙教训（PMX 刚体 → Bullet/PhysX 解算 → 写回 .anim）

路线演变（据记忆）：最早走 Blender 刚体；2026-09 初查明 MMD 本身就是 Bullet、差在配置层，改用原生 Bullet 命令行解算器；9 月下旬的记忆里已出现原生 PhysX 批解算（GPU）的试验批次。当前发布默认走哪条，记忆没写明，以仓库为准。Blender 物理路已退役，相关条目保留是因为判据在原生路同样成立。

用法：每条按"现象 → 真因 → 判据"写，【Bullet】【PhysX】标适用解算器，无标注为通用；数字是当时某角色某动作的量级参考。调参前先扫文末"别再试的方向"。

## 一、解算前的数据移植

**1. 碰撞组不移植，任何参数都救不了；且 PMX 掩码位 1 = 碰撞**
现象：206 个刚体全落同一层，rest 下 1823 对 AABB 重叠，裙子高频抽搐，子步、迭代、夹持、滤波全无响应。真因：导入漏读碰撞组与掩码；极性还分两层（mmd_tools 布尔数组 True=忽略，PMX 原始整数位 1=碰撞、双向许可），读反会得出相反结论。判据：参数全无响应就查数据，移植后重叠 1823→342；列出"能碰组"看常理，裙子除自己组外全碰才对。
来源记忆：pmx-collision-groups-must-be-ported

**2. 碰撞盒朝向不等于骨朝向，薄板方向整个错**
现象：277 个刚体盒与骨夹角中位 93.24°、最大 180°。真因：建盒只用骨 rest 朝向，没用 PMX 的盒旋转。判据：存"相对该骨 rest 的偏移"（同名骨 rest 朝向跨骨架最大差 0.08°，可搬），只切这一个字段做端到端 A/B，接缝 3.26°→1.58°。
来源记忆：pmx-rigid-orientation-must-be-ported

**3. 刚体筛选与回写方向：别只筛 hair/skirt，墙不写回，kinematic 不驱动骨，看漂移不看距离**
现象：按 hair/skirt 筛，洛茜漏了 69 个挂件（耳、尾、帽、爪等全僵死）；4 个 kinematic 耳朵被拷位置，骨长变化率中位 1683%。真因：关键字太窄；身体墙写回 .anim 会让角色瘫掉；physicsMode 每个刚体自带（0 骨→刚体不回写，1 刚体→骨且位置旋转都写，2 只写旋转），kinematic 反过来驱动骨是循环。判据：--dump 分组计数对照 PMX 可见挂件（"没物理"也可能是源模型没做，要回去数源骨）；模式 1 看漂移（mmd_tools 原生基线漂移 0.0000，距离却有 0.3052）；跳过 kinematic 后骨长变化率 2.41%。
来源记忆：physics-rigid-keywords-and-body-walls, mmd-physics-mode-decides-writeback

**4. 源数据的边界值会让 PhysX 拒收，要在模型层修**
现象（鸣潮批次）：半径 0 的球报 invalid shape size；32 个关节限位恰为 ±π 报 swing limits outside PhysX range；101 个关节角弹簧 y 分量 -0.3 报 negative spring stiffness；不挂骨的胸链刚体让旧 contract 抛错。真因：源数据边界值；钳制写在序列化时会报 Angular bounds overwritten。判据：在 Rig 里修 model（限位收进 ±(π-1e-4)、负弹簧按 0、零尺寸扩 0.001、不挂骨刚体跳过），并核对旧 92 人场景不变。
来源记忆：wuwa-import-plan-20261008

**5. 作者的零宽角锁别全解开，胸/耳/饰件会在重力下垂成摆**
现象：比悠卡系胸下垂 78°、汤汤耳 111°。真因：响应层把 lo=hi=0 的关节一律解成角自由，而这些是作者的刚性挂件。判据：按族保留零锁（soft_body、ear、accessory）；hair/skirt/cloth/ribbon 本就靠解锁才动；验证用父相对朝向对 PMX rest，别用 .anim 的 rest（本身就是下垂姿态）。
来源记忆：phys-zero-lock-unlock-causes-sag

## 二、写回与坐标空间

**6. 视口里物理完整、进游戏裙子不动：运动在位移通道，写回只采了朝向**
现象：洛茜裙 129 根在 .anim 里全只动 0.01°，稳定得可疑。真因：裙和披风的运动几乎全在位移里，导出只采朝向；物理骨没有动画通道，位置必须取父链 FK，钉 rest 会把深链末节甩飞（峰值 81.46→17.66）。判据：日志"跑完 N 帧，取到 M 骨"证明解算有数据，问题在其后；看分布不看超阈计数（>5 单位顶点 1447→5568 反而是修好了）。
来源记忆：viewport-ok-but-anim-wrong-two-paths, physics-bone-writeback-needs-fk-position

**7. 发尾一个球不跟动：torso 网格顶点蒙在 head 所属的物理骨上**
现象：发尾有球停在静态。真因：两边都蒙皮的骨归 head，物理只写进 head anim。判据：同名骨 torso/head 两侧世界位置最大差，阈值 2 单位（身高约 146），44 人里 18 人 76~207、其余为 0，颈骨 25.64 人人都有要排除；修法是按角色开 dual_side_writeback。
来源记忆：ck3-dual-side-writeback-head-owned-tail-balls

**8. 写回后处理在 ±180° 附近必出错：低通、去自转、循环接缝都要符号对齐**
现象：log map 低通把摆幅近 ±180° 的尾巴滤出 263° 假摆；去自转在摆角≈180° 逐帧翻转，写出一帧 173° 跳变；循环接缝淡入一帧跳 139~150°。真因：旋转向量与最短路径 slerp 在 ±180° 不连续。判据：1.5Hz 零相位低通滤符号对齐后的四元数分量再归一化；近奇异骨整段不去自转；接缝两序列先时间符号对齐、整体只选一次符号；验收看 .anim 相邻帧最大转角。接缝还要交叉淡入真实续录段（--loop-extra）：单侧拉向首帧虽把首尾差压到 0.04°，接缝速度跳变仍 1.63°/帧。
来源记忆：phys-writeback-lowpass, phys-seam-crossfade-not-onesided-pull

## 三、抖动的机制与杠杆

**9. "抖"至少四种机制，按组均值分不出，先判是不是受迫响应**
现象：奇塔一人就有三种；艾尔黛拉裙 kick 18.4% 其实是如实响应马背颠簸。真因：kick% 把受迫响应也算成抖动。判据：verify_phys_kick.py 按骨链出指纹（sust%≥50 为颤动/参数，否则踢击/穿插），须对照无物理基础动画剔骨；裙骨步长与脊椎角速度相关 |r|>0.5（该例 0.856）是受迫，杠杆在激励侧，<0.25 才是自激。
来源记忆：phys-jitter-has-three-mechanisms-verify-by-chain, phys-skirt-kick-is-forced-response-not-jitter

**10. 【Bullet】孤立一根骨高频抖、周围没墙：查弹簧刚度/质量比，别调阻尼**
现象：洛茜监禁动画根骨不碰任何墙，逐帧转角峰 111°。真因：錘配重关节弹簧刚度 1e5、质量 1、子步 1/120 s，显式弹簧 k·dt²/m=6.9>4，每子步自激发散。判据：改隐式 Spring2（limitIfNeeded 自动限刚度），抖动 34.4→2.9、待机裙抖 0.70→0.00，已进默认。
来源记忆：bullet-explicit-spring-self-excites

**11. 【Bullet】限位行 ERP 是抖动主杠杆，但只能降有活动范围的轴**
现象：抖动是 3~6 Hz 的单帧踢击后衰减（突跳 10~57°），不是逐帧正反颤。真因：限位停止行默认用世界 ERP 0.2，越界量一个子步内被硬弹回。判据：只降 lo≠hi 的轴（默认已改 0.02，idle 系耳踢击帧 21%→6.7%）；全局降到 ≤0.1 会让接触压过全锁关节，洛茜裙分离率 74%→153~174%。疑似回归先做无关微扰：erp 0.19 就能复现 11.6%→227%，基线是刀锋态运气解。
来源记忆：bullet-limit-erp-is-the-jitter-lever

**12. 裙抖的碰撞侧杠杆是"混合组"和"免碰组对"，代价是支撑**
现象：绛雨裙抖；利塔拉内裙 idle 踢击 58%，求解器参数全持平。真因：绛雨 cloth_loft 是墙与动态体混在一组，关 Lf+Rt 共 16 条追平"只留裙子"基线（那个要关 149 条）；利塔拉是全锁盒链与腿墙一接触就互踢，内裙免碰腿墙后 58%→0.56%。判据：混合组里墙走 no_collision、动态体走 follow，身体墙按日文骨名白名单保留（摘掉后 max 24.43→115.04）；免碰后用轨迹探针核"嵌腿 >3 倍厚度"占比；rest 穿透只是相关量（收墙到不嵌入，踢击仍 61%）。
来源记忆：phys-mixed-groups-are-the-jitter-lever, phys-kick-means-rest-penetration-check-collision-config

## 四、裙子与坐姿

**13. 坐姿裙抖是腿墙静态挤压的极限循环，穿透越浅越凶；有效杠杆是 linear slop**
现象：sitting 第 0 帧最深嵌入 0.89 倍厚度、零格超厚，却抖得最凶。真因：腿墙把裙格静态压住，没有静止平衡点；【Bullet】m_linearSlop 默认 0，求解器在静置接触上一直争最后一点穿透。判据：极限循环下 kick% 天然偏高，看峰值与末端步长；"浅（<0.04）且久（≥50% 帧）"的裙-墙接触对数跨动作单调（坐姿 32 / idle 27 / spin 0），只能在同一 slop 值内比；--linear-slop 0.03 使末端位移 p95 0.1189→0.0847（-29%）、kick 26.2→18.2，dance_spin 也同降，代价是裙-墙穿透中位 0.0044→0.0302、静垂型 writing 回归，2026-09-16 定为 Bullet 路全员默认。
来源记忆：phys-sitting-jitter-is-static-squeeze-limit-cycle, phys-shallow-sustained-contacts-are-the-jitter-metric

**14. 解算要有"从 rest 渐变到第 0 帧"的入场段，预热圈数救不回来**
现象：坐姿第 0 帧腿已抬起，裙格被瞬移进腿墙内或另一侧。真因：原先瞬移后直接积分，预热每圈重复同一个错误初始拓扑。判据：--intro 60（0.5 s）把运动学墙从 rest 插值到第 0 帧、动态刚体靠物理跟随，kick 49.7→31.2、峰值 163.31°→29.71°；720 步反而 123.10°（墙太慢把裙扫飞）。
来源记忆：phys-needs-intro-transition-from-rest

**15. 压坐姿抖动的"软化"都在拿穿腿换，穿腿要用对的尺子量**
现象：【PhysX】软接触 60Hz 临界阻尼把 4 副本 HF 合计 2.16→1.74，代价是 cyu 坐姿三角交叉 1.8K→10.7K；全局慢推开让裙整片穿腿，按穿透深度分级推开后穿腿 26→0。真因：迁移动画使部分裙弹簧目标落在大腿另一侧，裙靠推开速度顶住拉力。判据：三角交叉数对"整片穿过去"是盲的（穿过后反而降），用 passthrough_check 对照参考版；深度阈值 0.1/0.4 时林 idle 尾巴 8 份副本 4 份"卡住后弹开"，降到 0.03/0.15/1 后 0/8。
来源记忆：phys-compliant-contact-60hz-cuts-squeeze-jitter, phys-depth-graded-depenetration, phys-writeback-lowpass

## 五、头发、披风与挂件

**16. 斗篷搭在手臂上：屏蔽手臂墙只能收 hair/skirt，空闲掩码位要照抄原位**
现象：第一轮屏蔽所有长期贴手臂的刚体，洛茜 idle 分离率 5%→44%；第二轮斗篷仍崩到 52%。真因：斗篷撤掉手臂就掉下来；PMX 掩码"未列出的组默认 1"，手臂搬进空闲组后，原本不撞手臂的刚体全开始撞。判据：空闲位先清再按命中补；改掩码后拿"不该受影响的组"当对照，指标应与基线逐位相同。
来源记忆：physics-arm-shield-mask-semantics

## 六、评估与判据方法

**17. 测物理必须用躯干会动的动画，名字猜不出来**
现象：用 idle 测裙子量出"角速度 0.000°"，误判物理坏了，白测三档夹持角。真因：idle 的 spine_low 最多动 1.07°，裙子没外力本就该静止；洛茜 345 个动画里 219 个躯干运动 <5°、仅 14 个 ≥30°。判据：先跑 rank_anim_motion.py 排名，只量 spine_low→waist→groove→center 这条链，不量四肢。
来源记忆：physics-test-needs-torso-motion-anims

**18. 混沌动作的单次对比不是测量，要用副本系综**
现象：同一 scene 同批放 6 份（只差网格原点），坐姿陈千语包 HF 0.060~0.245；单次数字宣布"穿模 -30%"不可信。真因：坐姿裙是无平衡点的极限循环，把浮点舍入放大（PhysX 单实例重跑逐字节一致，批内换位置就是换样本）。判据：ensemble_hf.py 每动作 4 份副本同批，报均值±std，|差|>2σ 才算差异；副本离散度本身就是"会抖"的指标。
来源记忆：phys-chaotic-actions-need-replica-ensemble

## 七、工程与耗时

**19. rc=0 与没有 Traceback 都不等于烘完，必须核对产物计数**
现象：芙洛伦 345 个只出 326 个，分片在第 902 帧崩溃，无 Traceback、脚本照报成功；另一次烘焙期间重写了模型包，两人计划为空却 rc=0。真因：C 层崩溃；计划读的是模型包 .asset；shell 里 `$?` 写在 `$(date)` 之后永远是 0；会话中断收走控制台，后起解算器以 0xC0000142 秒退。判据：[bake] 计数合计必须等于目标数（grep 加 -a）；烘焙期间只改 tools/docs/work，不跑写模型包的生成器；长链用独立进程，先取 rc 再拼日志。
来源记忆：blender-long-anim-crash-silently-drops-shard, phys-bake-reads-asset-no-concurrent-build

**20. 耗时按关节数估，瓶颈是串行写回不是 GPU**
现象：按芙洛伦（71 关节，4.6 min）外推"27 人 2 h"，实际 9.5 h。真因：约 3.1 s/关节；单角色 331 动作在一个进程里串行写回；ThreadPool 受 GIL 限制只跑 6~7 个解算器，改进程池后 md5 逐字节一致；显存不够是硬失败。判据：估算前数 len(model.joints)；体积约 380 MB/人，30→15fps 省 49%，后处理要写到独立目录，原地改会让续跑哈希失效。
来源记忆：phys-full-batch-cost-is-cpu-bound

**21. code_fingerprint 决定 --resume 能否用，改代码要排在批次之间**
现象：改 moe_bullet_bake 或 bone_twist 后旧根不能再 --resume，批次中途改会让 44 人全判失效。真因：指纹覆盖这些文件；moe_chars 虽不在其中，但批次启动时只读一次。判据：失败的人另建新根只烘他们、成功结果原样保留；并发 8 个解算器曾整机崩溃（queue.json 被截成空文件），改 --jobs 6；同角色多变体不能并行（共用 _io）。
来源记忆：wuwa-import-plan-20261008, phys-writeback-lowpass, phys-per-action-override-mechanism

## 八、Blender 路遗留经验（该路已退役）

**22. Blender 刚体无法 GPU，复用装配有坑；碰撞过滤引擎层锁死**
现象：装配 143 s 比解算 66 s 还贵，提到循环外后全量 1.8h→25min；层掩码方案漏屏蔽 13261 对。真因：内置 Bullet 纯 CPU；Blender 过滤是对称单字段 AND，表达不出"同组不撞"，只能逐对建 NonCollision 约束（须按 rest AABB 剪枝）。判据：换动画前必须 ptcache.free_bake（各动画夹持计数应不同，相同就是复用旧解）；清装配要连 collection 一起删，否则解两套、抖动加倍；切到更短的动画会 C 层静默崩溃（日志帧数从大变小后紧跟 crash.txt，降并发无用），只能一条一进程。MMD 本身就是 Bullet，差在重力 98、禁休眠、6DofSpring 零限位、120 Hz×10 迭代这些配置，这是换原生解算器的依据。
来源记忆：blender-physics-no-gpu-hoist-assembly, pmx-collision-groups-must-be-ported, mmd-is-bullet-too-config-differs

**23. Blender 姿态求值与空间陷阱：pb.matrix 陈旧、刚体要落座、跨空间比距离差缩放倍数**
现象：basis 45.60° 而 pb.matrix 距 rest 0.000；刚体按 rest 摆、骨在动画姿态，错位最大 12.5；跨空间比绝对距离差 7.19 倍。真因：update() 不求值骨姿态，只有 frame_set() 触发；rig 缩了 1/SCALE，pb.matrix 在骨局部空间而 matrix_world 在世界空间。判据：basis 转角与 pb.matrix 距 rest 须同时为零或同时非零；落座 = frame_set→刚体摆到骨当前位姿→枢轴一起搬→free_bake；距离用相对分离率 |当前−rest|/rest，每个测量量先写清所在空间。
来源记忆：blender-pb-matrix-is-stale-until-frameset, rigid-must-reseat-to-current-pose, bone-local-vs-world-space-scale-trap

## 九、别再试的方向

- 扫求解器参数压裙抖（利塔拉内裙：频率、迭代、隐式弹簧 ERP、驱动增益）：hz240+iters120 反升到 70~72%（基线 58%），抖动是碰撞几何冲突每步被推出，不是求解精度。（phys-skirt-jitter-collision-levers-exhausted）
- split impulse 阈值往 0 挪：kick 26.2→40.6→46.4 单调恶化，浅接触改走不受阻尼约束调解的 push 通道只会被硬推。（phys-shallow-sustained-contacts-are-the-jitter-metric）
- 软接触刚度与入场期缩墙：刚度 300/1000 把裙子冻死，缩墙三档都不如不缩，等于暂时撤掉支撑。（phys-sitting-jitter-is-static-squeeze-limit-cycle）
- 接触摩擦 0.8 及其与免碰叠加：max 25.23→14.86 但 |r| 0.859→0.416，压掉的是如实响应。（phys-skirt-jitter-collision-levers-exhausted）
- 按行切分免碰腿墙：免碰行 2~15 穿腿 12.1%，比免碰行 0~4 的 10.3% 还差，裙是链，上部穿腿会把行 0~1 拖进去。（phys-skirt-jitter-collision-levers-exhausted）
- 放开裙行 0 的焊死或给开口端加角阻尼：放开后 max 25.23→65.61，行 0 是整片裙的刚性基座，裙骨阻尼已 0.990~1.000 没有加码空间。（phys-skirt-kick-is-forced-response-not-jitter）
- 缩小或按蒙皮重拟合大腿胶囊治"裙被夹"（Z1 证伪）：裙压在大腿上表面，解算器穿透变小只因墙缩了，按真腿皮量嵌入帧占比反增（wtq idle 0→17%）。（phys-thigh-pinch-is-skirt-on-top-not-between）
- 加大驱动增益指望整条头发飘：增益 1/8/16 的头发摆幅中位只有 0.24°/0.62°/1.16°，剩下的杠杆是关节弹簧刚度与阻尼。（phys-drive-gain-weaker-than-wind）
- 拉长速度/转动半衰期（0.3 s）让头发尾巴变软：梨诺尾巴甩鞭（jitter 32.7°），变软改弹簧 Hz 与阻尼比；终末地"硬"不是限位问题。（phys-endfield-stiff-is-spring-not-limits）
- 【已取代】原地写回 .anim 再靠档案回注：一次 build_all 就把 357 个烘好的 head anim 抹成 4，机制已于 2026-09-06 退役，现靠物理包与基础包目录分离；"物理产物必须与会被重新合成的基础产物分文件"仍成立。（phys-bake-must-be-archived-outside-anim）
