# 教训汇总 2：动画重定向与肖像动作

覆盖「原版 .anim 重定向到萌系骨架 → head/torso 配对 → state 注册 → 手部/道具/相机」这段流水线。适合在「数值全绿但画面不对」或「零报错但不播、不渲染」时按症状查。记忆是时点观察，脚本细节以当前代码为准。

## 一、重定向的参考系与 rest

**.anim 的 rest 是该动作的第 0 帧，不是 bind pose**
现象：增量法出僵直手臂，肘角 179°（原版 rest 左肘 98.3°）。真因：带通道骨 rest→frame0 夹角全为 0.00°，idle 与 AI_bold 的 rest 有 52/59 根不同。判据：传绝对世界朝向 + 由骨指向子骨方向求的轴向修正，逐帧比世界空间关节角（修后四肢 0.00°）；「转了多少度」这类自比指标增量法也全绿。
来源记忆：pdx-anim-rest-is-frame0-not-bindpose

**需要「中性姿态」的地方不能用当前 clip 的 rest**
现象：坐姿产物直腿站着、尺度比随源动画变、坐姿留在站立高度，是同一根因的三个症状。踝→胸高度比 idle 0.9115 / 跪姿 1.6962。判据：基准一律锚 idle 的 rest；验收量世界空间大腿方向（坐姿应水平），「相对首帧最大转动」在静止的坐姿里报 1.00°，查不出。
来源记忆：anim-neutral-must-anchor-to-idle-not-clip-rest

**摆位骨 body_root 的旋转必须一起传，修法进脚本不等于产物修好**
现象：dance 类腰部打结，24 个角色数字完全相同。真因：摆位骨无骨向子骨，CORR 为 None 被过滤，spine_low 178.3° 与两根 hip 各吞下原版 body_root 的 179.6°。判据：多出的量恰等于某祖先骨在原版的旋转，就是祖先旋转没传，看整链分配。修法进脚本后装机仍 178°，因基础动画产物是修法之前生成的：改算法后必须重跑 build_all --with-anims 并重烘物理。
来源记忆：anim-body-root-rotation-must-transfer

**q 与 t 共用父骨「当帧」参考系，rest 下自检无效**
现象：物理骨 q 泄漏身体旋转（cloak 偏转 34.98°，视口弯折仅 1.42°），被夹持压回 10~12° 掩盖。真因：q 长期相对父骨 rest；rest 姿态下两者不可区分，「第 0 帧 q==rest q」自检照样通过。判据：同一次解算里取两条路的同一量逐骨逐帧比（修后中位 0.000°）；扭转不改骨长与轨迹，须做 swing-twist 分解。
来源记忆：anim-q-and-t-share-parent-current-frame

**anim 里的骨必须存在于该 mesh 才生效**
现象：把 root 映射到带蒙皮的 bn_m_center，全身凭空多 24° 摆动，像「躯干和头不同步」。真因：female_body_idle 的 71 根骨里只有 root 不在 mesh 中，引擎按名查不到，原版直接丢弃该通道。判据：扫 mesh 骨名做存在性判断，别按骨名猜用途。
来源记忆：ck3-anim-bone-must-exist-in-mesh

## 二、左右镜像、骨轴与滚转

**原版镜像左右骨的局部轴而 MMD 不镜像，病根在腿链不在踝**
现象：右脚从小腿撕开（跨踝形变最坏 352%），裙包歪斜；两条链翻转的侧相反，像「左右接反」。真因：两侧 fm 相同、fv 相反，rot_between 给出差 180° 的 C。在踝上补三次、右侧 fv 取反（反而 ~175°）都无效。判据：腿链整体改 rest 对齐，撕裂 352%→0.00%；量骨向夹角或跨接缝形变率，四元数差角含轴向约定分量会骗人。
来源记忆：mmd-vs-ck3-mirrored-bone-axes

**骨向对齐定不住绕骨向的滚转：脊椎、腕、指节、指尖同一类病**
现象：上半身拧半圈（spine_low 第 0 帧 72°/67°，正常 23~29°）；手掌翻 82~146°（92 人中位 27°，看不出）；指节扭 165~180°。真因：CORR 只用 rot_between(骨向)。判据：加第二向量（肩线 / 指节线=食指根→小指根，阈值 45°），指尖仅在轴差 >20° 时改，其余角色产物逐字节不变；手部验收看逐节带符号弯曲角 vs 原版（修后差 ≤1°）；拇指仍有 40~100° 扭转未处理。
来源记忆：pmx-local-axis-spine-roll、wrist-rot-between-leaves-palm-roll-free、finger-roll-knuckle-line-align、fingertip-fallback-needs-rest-axis-delta

**脚尖外展的偏航要加整条腿链，只加踝会拧坏所有动作的脚踝**
现象：44 人所有动作脚踝皮肤被拧，而 toe_out 与腿链弯曲数字照样「修好」。真因：偏航只加在 ankle/ankleD，膝没转，踝相对膝恒定拧 +25/−19°（正常在 ±10° 内）。判据：偏航加到 LEG_REST_ALIGN 本侧各骨，验收量踝相对膝扭转与套姿态后的皮肤。开关要按游戏自动透传，手动命令行 flag 经 import_chars 重做会静默丢。
来源记忆：wuwa-foot-turnout-on-by-game

## 三、head/torso 配对与帧数

**头躯干是独立 entity，每个身体动作要配一份带脊椎的头部动画**
现象：躯干前倾而头留原地（grief 脊椎 23.41°，头错位 21.49 单位≈36% 头高）。真因：attach 是 bn_m_root→bn_m_root，它在身体动作里是静态骨。判据：每动作生成 moe_head_<base>.anim（表情 fx + 该动作自己的脊椎）；帧数和通道有无都照抄身体（跟表情走会 361→90 抽帧；身体没有的通道要删掉，否则头随呼吸浮动）；渲图给两件 mesh 各自的动画，一份 anim 驱动两件永远同步。
来源记忆：ck3-head-torso-need-paired-anims

**头身分离的真凶是 state 表里的异名配对**
现象：anger 头身分离。真因：.asset 把头身配了不同名动画（原版给胖体型的 _var2/Fat，9 组），头脊椎却从同名身体动画嫁接。数字：同名 506 组差 ~0.04°，异名 9 组 7.48~23.49°。判据：配对必须从 .asset 实读，按同名比对恰好排除问题组而得出「零差异」。
来源记忆：ck3-head-torso-need-paired-anims

**同名 state 挂多个变体：dict 会覆盖、下标会错位、头身各自随机**
现象：「一半时间头身对得上」；wedding_happy_cry_loop2 头退用 body_idle 脊椎，差 80 帧。真因：dict 推导后者覆盖前者；head 与 torso 变体注册顺序不同，按下标配整体错位；两 entity 各自随机，两侧集合相同也脱节。判据：同名 state 是否只挂一个动画（全表 17 个不合格）；按 id 名对位；collapse_variants 必须排在 reconcile_pairs 之前。
来源记忆：ck3-one-state-registers-many-variants

**往配对文件补通道时，写回帧数必须是目标自己的**
现象：moe_head_idle 的 242 帧被压成 241，wedding_happy_cry_loop2 的 322 帧压成 242。真因：min(cF,F) 同时是 write_back 的输出帧数，把通道长度当成了文件长度。判据：companion 保持自己的 cF，摇骨数据短了用末帧补齐；烘焙前后对每个 companion 比帧数，不等即缺陷。
来源记忆：ck3-companion-write-must-keep-own-framecount

**改了 torso 重定向，head 必须重嫁接**
现象：所有动画头比身子靠前 3~4 单位，脖子侧面撕开。真因：前倾从 groove（19~22°）挪到 spine_low，支点高 12 单位；旧 head 骨数恰好等于新 mesh，零报错。判据：verify_all_anims 的「配对脊椎 rest 一致」硬闸（会报 bn_m_groove Δq 19.06°）；验收用 --no-physics，因物理补丁包同名覆盖 head 会换回旧版。
来源记忆：ck3-head-torso-need-paired-anims

**torso 网格顶点蒙在 head 所属骨上，物理只写进了 head 侧**
现象：发尾一个球不跟动。真因：skin_owner_map 对两边都蒙皮的骨归 head，torso 上的球随 body anim 停住。判据：同名骨 torso/head 两侧世界位置最大差，阈值 2 单位（身高约 146），44 人里 18 人 76~207、其余 0，颈骨的共有差要排除。渲图和关节判据都看不出；按角色开 dual_side_writeback。
来源记忆：ck3-dual-side-writeback-head-owned-tail-balls

## 四、state 与动画命名

**animation id 与 state name 是两个命名空间，循环靠 next_state 自跳转**
现象：动画注册了却永不播且不报错；实测 6188 条 Unknown animation state。真因：state 里写了 animation id（head_emotion_angry），游戏请求的是 state 名（emotion_angry）；循环写法是 looping = no + next_state 指回自己；每个正式 state 还要配 _entry。判据：新增 state 名前先 grep 原版 asset（greeting 零命中，鞠躬实为 throneRoom_bow1/2/3）。
来源记忆：ck3-anim-id-vs-state-name

**一个动作有四层名字，portraitEditor 只搜 animations.txt 的块名**
现象：idle_calm、greet_bow 在 portraitEditor「搜不到」。真因：块名、state 名、animation id、.anim 文件名四层，只有第一层可搜；块名 throne_room_bow_1 与 state 名 throneRoom_bow1 不是大小写变体，须照原版抄。判据：查 error.log 的 Unknown animation state 要连引号精确匹配，搜 idle_entry 子串会命中 56 次原版名。
来源记忆：ck3-portrait-animation-four-name-layers

**完整动画集以原版 state 表为清单，手写映射补不完**
现象：手写 17 条只注册 120 个 state，原版请求 368 个 torso / 288 个 head state，缺一个刷一条日志（5128 条），情境下角色僵在上一姿势。做法：state 照抄全列，.anim 按基名折叠（676→345）；折叠正则须同时吃 angryFat 与 angry_Fat，否则 399 vs 345；头身配对从 .asset 实读（371 组里 87 组名字不同）。
来源记忆：moe-full-anim-set-from-vanilla-state-table

## 五、循环与接缝

**循环接缝的首尾差就是画面跳变，渲图看不出：手臂 IK 曾让 idle 定期抖动**
现象：动作循环不连贯。真因：非 idle state 靠自跳转模拟循环，引擎接缝不插值；默认开启的手臂 IK 让 moe_idle_calm 首尾差 23.43°（集中在 bn_m_r_wrist）。判据：量首尾帧四元数夹角（tools/verify/check_anim_loop_seam.py），关 IK 后 0.0x°，449 个循环动画接缝最大 0.12°。
来源记忆：arm-ik-caused-idle-loop-jump、moe-full-anim-set-from-vanilla-state-table

**非循环动画不能消接缝，is_looping 写对了不等于接上了**
现象：stayback_start 的 23.97° 首尾差被压成 0.00°，日志看着像成功。真因：接缝、扰动、滤波三处各自只看 loop_tail，绕过了判定；后来又发现表的键是完整 animation id（= .anim 文件名 stem），传裸 state 名查不到就走默认「循环」。判据：grep 判定函数的所有调用点；日志区分「跳过」与「做了结果是 0」；指纹是记录帧 0 单尖峰、8 帧内落回。
来源记忆：ck3-nonlooping-anims-must-not-close-seam

## 六、手持道具与手部 IK

**手持道具与合掌要做双手关系 IK，道具偏移也要缩**
现象：镣铐类道具左腕漂 10~16 单位；之后「手持武器略偏」而 IK 残差是 0。真因：我方臂长 33~39、肩宽 16~18，原版 47.7/30，两手间距随比例缩而道具 mesh 是原版尺寸；偏的是锚点，bn_l/r_prop 相对腕的向量要乘 HAND_SCALE（flo 0.593）。判据：逐帧 FK 残差 + 肘角 + 首尾 dq；道具沿掌轴归一化位置原版 0.72~0.83、旧 1.32~1.51、新 0.78~0.90；持道具手要实读 animations.txt 且只看直接子项，不靠名字猜。
来源记忆：ck3-shackles-are-rigid-prop-need-left-arm-ik

**prop 骨只建在躯干，head 动画要抄全部同名驱动骨**
现象：书飘在体侧且侧立，零报错；bn_r_prop 世界位置躯干与头相距 35.01。真因：原版 head mesh 无 prop 骨，我方头躯骨表相同，accessory 只按名字找骨；去掉头上的 prop 骨后仍错位，因 198 根同名骨在 head/torso 间共享姿态。判据：量同一根骨在两份 anim 里的世界位置差，别量到父骨的局部距离（两边都是 8.73）；「head 不渲染时正常」是特征；定了修法必须同时加 verify 硬闸，否则产物照旧。
来源记忆：ck3-prop-bones-must-be-torso-only

## 七、相机锚点

**相机锚骨必须钉住世界位置，否则相机跟着身子晃**
现象：portrait editor 里相机随身体动（laugh 漂 17.21、conversation 4.11、idle 1.22 单位）。真因：原版每条 anim 给 camera_torso_look_at 自己的 t+q 通道让世界位置恒定，我方的挂在 bn_m_neck 下无通道。判据：逐帧反算局部 t/q，FK 探针漂移 0.000；head 侧必须列进 BODY_ANCHORED，因恒定补偿被 _drop_constant 压进 rest 后通道消失，只抄「驱动骨」会让两 entity 的同名骨给出两个位置。
来源记忆：ck3-camera-anchor-bone-must-be-world-pinned

**原版相机节点按「原版骨表下标」解析到萌 entity**
现象：镜头落到原点，像从脚底往上拍。真因：引擎把节点名按原版骨表换成下标再取骨：camera_torso_look_at=75、bn_sp_lumbar=2 对躯干载体，bn_h_head_mid=22 对头部 entity（5 骨 rig，22 号越界）。判据：躯干载体按下标放骨，头 rig 补到 23 骨、22 号放自造名，萌 entity 不能新增原版同名骨；动画按下标绑定，只挪骨序不改动画会让 59 根脸骨错位。
来源记忆：ck3-camera-node-resolves-by-name-per-type

## 八、发布目录保护与流程纪律

**发布的 canonical 目录不可变，重生成会写穿它**
现象：build_all_anims 不带 --out-dir 直接覆盖已发布角色的 canonical 目录（721 个文件由 manifest 记 sha256），靠备份按哈希 721/721 恢复。真因：bake_dir(ck) 对 31 个发布角色返回 release_revisions 下的路径。判据：动手前看 bake_dir 是否发布路径，是则写暂存盘，确认差异只在目标通道后按通道移植，并先备份。
来源记忆：release-canonical-dirs-are-immutable

**发现产物缺陷，改上游那一步，不写补丁脚本**
现象：mesh 缺 bn_l/r_prop 时写了三个脚本并往 build_all 加一步，技术上通但被否决。真因：产物可整批重做，补丁脚本却永远排在某步之后每次构建都跑，还掩盖上游真问题。判据：先问「这东西本来该由哪一步产出」，正解在 bake_moe_expressions.py 的建骨段（.mesh 与模板 .anim 的共同导出点）；build_all 每加一行都要说清为什么不能并进上一步。
来源记忆：fix-upstream-never-patch-products
