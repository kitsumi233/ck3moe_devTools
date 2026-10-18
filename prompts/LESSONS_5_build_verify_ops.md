# 教训 5：构建、验收与运维

本文汇总构建顺序与装机链、验收与日志判读、长任务与进程、工具链与编码、仓库与发布、协作约定的反直觉教训，每条按「现象 → 真因 → 判据」写。记忆是时间点观察，先读末尾「当前状态提醒」，再决定哪些步骤还能照做。

## 一、构建顺序与装机链

**主构建脚本开头清空整个 mod 目录，它之后每一步都得补跑**
漏跑 shader 步则材质退回单面剔除，漏跑 register 步则 .asset 只剩 6 个 state（完整 3954 个），进游戏刷几千条 Unknown state，零报错。因为 build_moe_mod 开头 rmtree 整个 mod 目录。判据：split_mods 报「moe_shader 无文件」，或数 `state = {`；用 tools/build_all.py 一条命令并带自检。
来源记忆：moe-mod-build-order-matters

**构建、装机、启动不要串成一行**
构建自检失败后装机与启动照跑，坏产物让人白等 12 分钟。因为脚本先写产物再校验。判据：构建单独跑，确认退出码 0 与「自检通过」再装机；要串用 `&&`（PowerShell 5.1 用 `; if ($?) { ... }`）；自检拦下的是真问题，别绕过。
来源记忆：build-failure-must-stop-install-chain

**rc=0 加几百行「正常」日志，也可能整包没处理**
第二个游戏包的贴图三周从未转换。因为 main() 只读 sys.argv[1]，多传的目录被丢弃，「扫到 0 个文件」又被当正常出口。判据：看 DDS 时间戳是否晚于 PNG，别看日志有没有记录；参数用 sys.argv[1:] 并逐个校验。修后转换记录 474→894 条。
来源记忆：convert-textures-only-processed-first-game-package

**产物归属在写文件时声明，不要事后正则分流**
481 行正则分流掩盖了三个零日志的 bug：模型包缺自己的 .asset（单独启用什么都不渲染）、共用贴图按名字猜归包、物理通道按文件名挑。判据：finalize_mods 的五项自检就是回归测试，别绕过；同名文件靠 load order 覆盖，dependencies 与 ORDER 顺序是功能性的。
来源记忆：moe-out-owns-product-placement

**生成器产物不是已发布形态，新包先重放后处理**
新包跑 vnode_fix_all 断言 `nfile == 2*len(cars)` 失败，因为另一台电脑对已发布包做的 6 项后处理没回流生成器。判据：先跑 replay_pack_fixes，对旧包重放应与发布版 0 行差异；沙盒里先 grep 写死的 mod/ 路径与 relative_to(ROOT)。
来源记忆：generator-lacks-shipped-pack-postfixes, wuwa-import-plan-20261008

## 二、验收与日志判读

**日志「0 条错误」可能属于改动之前的进程**
改完 accessory 后 grep 得 0 就判修复，那份日志是旧进程写的；Git Bash 的 tasklist 返回空而进程其实活着（CPU 1006 秒、内存 6271 MB 且在涨）。判据：进程 StartTime 晚于改动，日志尾部是本次运行；存活用 PowerShell 的 `(Get-Process ck3 -EA SilentlyContinue | Measure-Object).Count`；加载期日志会停写，进度看 CPU 与内存，完整启动约 12 分钟。
来源记忆：ck3-log-check-needs-timestamp-and-pid

**缓存损坏与 shader 语法错的报错一模一样**
error.log 从 7~32 KB 涨到 4.4 MB，刷出 entrypoint not found 4136 次，我曾两轮误判成缓存问题。真因有二：强杀留下截断 .scache（或同一 userdir 同时起两个实例，14457 条缓存互相截断）；shader 注释里的 `]]` 提前闭合 PDX 代码块。判据：路径指向 shadercache 是缓存坏，指向 .shader 带行号是产物错；清缓存后日志仍逐字节相同（4437990）就是确定性错误；启动后进程数必须为 1。
来源记忆：force-killing-ck3-corrupts-shadercache

**截图工具失败不报错，只给内容不对的图**
抓到别的程序、全黑、静态加载画面。因为 ImageGrab 抓前台窗口，独占全屏 D3D 抓不到，窗口标题有了不等于加载完。判据：抓前先提前台；「加载完成」以 error.log 出现立绘或基因条目为准，还要进存档。
来源记忆：screenshot-must-verify-window-identity

## 三、长任务与进程管理

**数小时长链要独立 console，监控覆盖所有终态并到期重挂**
44 人重生成时后台 shell 被回收，之后每个新子进程 0 秒退出（0xC0000142），42 人全失败；监控只 grep 成功串，30 分钟到期又没重挂，跑完数小时后才发现。因为子进程继承父 console。判据：用 Start-Process 起独立 console；别用 `Start-Process bash -lc`（cd 中文路径失败会静默空转），改 .cmd 启动器里 `cd /d` 直调 bash.exe；监控同时匹配 FAIL，或读每角色 status.json。
来源记忆：long-chain-needs-own-console, convert-textures-only-processed-first-game-package

**NtSuspendProcess 是计数挂起，挂几遍恢复几遍**
挂了两遍，resume 一遍后 CPU 时间不动也不报错。判据：记下次数并恢复同样次数，恢复后看工作进程 CPU 时间与产物文件数在涨，别看命令返回成功；暂停长任务优先挂起而非杀进程，可原地续跑。
来源记忆：ntsuspend-is-counted-resume-twice, never-stop-bake-myself

**批次进行中别改进了代码指纹的文件，登记表只在启动时读一次**
改烘焙器代码后 --resume 会让 44 人全判失效；改登记表，已启动批次照样失败。判据：等批次结束，失败者另建新根补烘；核对 run.log 里修复生效的标志行。
来源记忆：wuwa-import-plan-20261008

**云端 GPU 烘焙机：清单漏了导入期依赖，零报错得到错结果**
云端 56 个角色 329/331 个动作按循环烘（本地正确 215/331），116 个非循环动作被多跑几圈并闭合接缝。因为上传清单漏了 .asset（is_looping 查不到一律当循环）、导入时读的清单 json、逐角色 report、Blender 扩展目录。判据：看 prepared.json 的 loops 计数。另外：远程会话里起的进程随会话被杀；--jobs 24 在 188G 机器上耗尽可提交内存（页面文件仅 5G），并发按可提交内存定。
来源记忆：cloud-bake-server-setup

## 四、工具链与编码坑

**经 Bash 工具传 heredoc 会吃反斜杠**
`\n` 变真换行；`\w+` 变 `w+`，正则仍合法却匹配为空，脚本报成功。是传输层吃的，加 `'EOF'` 也一样。判据：含反斜杠的内容用 Write 写成文件再执行，用 `chr(92)` 拼，或补丁末尾 assert 目标行幸存。
来源记忆：bash-heredoc-eats-backslashes

**Python 在 Windows 写的清单是 CRLF，bash 逐行读带 \r**
日志显示「torso 1/35、head 0/34」像生成失败，实际 ok=35 fail=0；计数恰为 1 是因为最后一行没换行。判据：计数与生成日志不一致时先 `cat -A` 看行尾；写时 `newline="\n"`，或读前 `sed 's/\r$//'`。
来源记忆：python-crlf-lists-break-bash-loops

**bash 的 GROUPS 是只读特殊变量，赋值静默失效**
`GROUPS="hair,skirt"` 之后展开成 197121，烘焙收到垃圾关键字；驱动骨数 594（中招）对 363（正确）。判据：改成非特殊名；日志的「物理组命中数」必须大于 0。
来源记忆：bash-GROUPS-is-readonly-special-var

**重定向后的子进程按系统代码页编码，一个 ✓ 就能让整步报失败**
12 步全跑完，却崩在最后一行 print 的 `✓`（gbk），看起来像整个构建失败，其实产物已写完。判据：父进程 reconfigure 成 utf-8，子进程环境设 PYTHONIOENCODING 与 PYTHONUTF8；先分清是打印失败还是产物失败。
来源记忆：tools/build_all.py 顶部注释

**MSVC/CMake 三个坑都是零日志或误导日志**
Bash 里 `cmd.exe /c` 调 vcvars 会挂住；UTF-8 注释在 cp936 下被当代码报语法错；路径含中文时外部源码 obj 名带绝对路径，链接报 LNK1181。判据：用 PowerShell 的 `cmd /c`；所有目标加 `/utf-8`；第三方库用 add_subdirectory 自成静态库。
来源记忆：msvc-cp936-utf8-build-lessons

**PDX 文本的 BOM 读写要对称**
实机所有角色都不渲染，3445 次 Failed creating shader state，因为原版 shader 带 BOM，用 utf-8 读后 BOM 被夹在 HEADER 之后第 21 行。判据：grep `Unexpected token`；读用 utf-8-sig 并拒绝中段 BOM；就地改 mod/ 文件时保持 BOM 与 CRLF。
来源记忆：cloud-bake-server-setup, no-more-generators-edit-mod-directly

## 五、仓库与产物组织

**只维护仓库 mod/ 一份，挂载与否看 Mounted 不看 Enabled**
.mod 写 userdir 之外的绝对路径时 7 个 mod 全没挂载，debug.log 却显示 Enabled。判据：看 `Mounted Data:.*moe_` 与 `Folder not found`；userdir 放 junction 指回仓库，同步代码绝不能 rmtree junction（会删穿到仓库）。
来源记忆：ck3-mod-dir-is-copy-not-symlink

**重组目录后，懒导入是 --help 冒烟抓不到的雷**
重组当天冒烟全过，一个角色的 345 条重定向却全数 ModuleNotFoundError，因为函数内懒导入没人跑到。判据：跑 `python tools/audit_repo.py`，它按 ast 遍历全部 import 节点并比对 sys.path 声明；ROOT 用 `Path(__file__).resolve().parents[2]` 推。
来源记忆：tools-layout-by-pipeline-stage

**移脚本前验证真实 import，「文档说被取代」不等于没人用**
一个脚本被移进 trash，实为现役烘焙器的共享底座；另一次移走数据文件，消费方「缺则跳过」，animations.txt 静默少 1662 行道具注入，构建照报 ✓。判据：ast 查 import，并 grep `Path(...) / "文件名"`；正确处置是切掉死的部分（1208→582 行）；可选输入缺失应自动生成或 SystemExit。
来源记忆：audit-must-check-real-imports-before-moving

**已发布目录不可变，校验链上的文件不能删**
对已发布角色，bake_dir 返回的就是发布目录（721 个文件的 sha256 记在 manifest），不带 --out-dir 重生成会写穿。判据：动基础动画前先看 `bake_dir(ck)` 是否落在发布修订路径下，是就写暂存目录再移植；manifest、修订证明 json、物理烘焙输入目录互相引用且含绝对路径，不删不搬，清理前后逐个核对 path+sha256。
来源记忆：release-canonical-dirs-are-immutable, disk-cleanup-20261006-layout

## 六、发布与外部平台（通用流程）

**「推送/发布」指上传创意工坊，git 只做本地提交**
在线远端 main 与本地 master 无共同历史，本地历史里有 2.8 GB 产物与贴图，不能推。判据：Core 走「暂存目录 + 内容清单」，清单只写内容目录与更新说明、不写 description（否则覆盖线上描述），上传前先升 descriptor 版本号，设好代理；模型包每个数 GB，要单独确认；原模型若禁止二次配布，先提醒用户。
来源记忆：no-online-git-publish-means-workshop, wuwa-import-plan-20261008

**工坊内容比仓库新时，从仓库发布或重建会静默回滚**
另一台电脑直接上传后，仓库 mod/ 在 7 个 mod 上全部落后，重建会还原去儿童类型、地面道具、滑条修复、20+ 处相机重指等。判据：发布或重建前逐文件比对工坊订阅副本与仓库，先同步到仓库（带备份）（10-08 已比对同步）。
来源记忆：workshop-ahead-of-repo-20261006

**Steam 命令行上传工具登录会挤掉 Steam 桌面客户端**
走客户端接口的描述推送全部返回 EResult 21，与描述内容无关，因为登录顶替了客户端会话（日志出现 Session Replaced）。判据：看到 21 先查客户端连接日志尾部是否 Logged Off；先传完内容再推描述。
来源记忆：steamcmd-login-kicks-steam-client

**创意工坊条目多语言描述有三个静默陷阱**
逐语言用命令行工具推，页面全是最后一种语言；只推描述会清空该语言标题；俄语 8664 字节被拒（EResult 8），其余 9 种通过。因为命令行工具忽略 VDF 的 language 字段，标题与描述是同一条记录，描述上限 8000 字节。判据：多语言走客户端接口并每次同带标题；推前 `wc -c`；VDF 里 `\"` 会破坏解析；用客户端读回核对。
来源记忆：steamcmd-vdf-language-field-ignored, workshop-update-tooling

## 七、给 AI 助手的协作约定

**长烘焙任务只监控和报警，不准自己停。** 内存紧张也只报数字并给建议，由用户决定（我曾在可提交内存剩 0.4 GB 时自行停掉整轮，白丢进度）。
来源记忆：never-stop-bake-myself

**发现缺陷改上游那一步，不写补丁脚本改已导出的产物，不加没必要的构建步骤。** 产物随时可整批重做；2026-10-07 之后另有定案，见「当前状态提醒」。
来源记忆：fix-upstream-never-patch-products, no-more-generators-edit-mod-directly

**构建、烘焙、打包每一步都要并行。** 本机 16 核要用满；重活默认进程池（纯 Python）或线程池（IO），输出按输入顺序收集，共享写目标先分配唯一写入者，并行版要与串行版比对产物 md5；Windows 进程池不能从 stdin 或 `-c` 脚本起。
来源记忆：build-steps-must-be-parallel

**平台账号的登录凭据（含验证码）由用户自己输入，助手不碰。** 需要登录时请用户在自己的窗口操作。
来源记忆：steamcmd-login-kicks-steam-client, workshop-update-tooling

**兼容性优先于开局便利。** 基础组合不加持地、书签、王朝、纹章、landed_titles、map_data 这类数据（PLAYABLE_STARTS=False），新入口走换装窗口或可选包。
来源记忆：no-bookmark-starts-for-compat

## 八、当前状态提醒

- 2026-10-07 起用户定案：不再用生成器覆盖 mod/，直接改 mod/，以创意工坊版为底。原因是生成器落后于工坊，重建会 rmtree mod/ 并静默回退另一台电脑补丁过的产物。它明确覆盖上文「改上游」与「按序跑 build_all」两条旧约定。
- 不要再对 mod/ 跑：build_moe_mod.py、tools/build_all.py 全流程、finalize_mods、publish_mods。改 mod/ 就地改文件，保持 BOM 与 CRLF。第一个这样做的改动是换装窗口的「取消cos」按钮。
- 仍可在沙盒里用：把输出根指到沙盒目录（MOE_OUT_ROOT）跑生成器出新包，再 diff、只手工并入新角色相关条目；只写 work/ 的导入与烘焙脚本（如 import_chars）；只读检查脚本（如 audit_repo）。carrier_pipeline、vnode_fix_all 只对新包跑且显式传包名。
- 已过期：10-07 当天与取消cos 配套的 tools/ 改动不要再跑；第一节描述的是生成器时代流程，只作说明依赖关系用。
- 装机曾要求显式传 `--no-physics`（2026-09-13 的临时调试约束，当时物理包独立加载，启用数应为 5，不带是 7）。**该记录已部分过期**：现在 `finalize_mods.py` 把物理动画物化进模型包，`--no-physics` 的含义是“从基础烘焙输入恢复无物理对照”，启用数不再是 5/7。约束只在命令行里，重跑装机不会提示；是否仍适用先问用户。
