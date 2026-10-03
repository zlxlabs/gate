### 2026-10-03 · 主机取码服务接入（W3a 实施）

**当前阶段**：implementing；代码、工作流、测试与文档完成并已提交，等待主脑验收与合并。

**本段结论**：`$GATE_HUB_GIT_MIRROR_DIR/SOURCE-MODE` 声明为 `service` 的主机上，
caller checkout、9 处工具自举与两个 base/head fetch 全部改走宿主取码服务，作业不再向
GitHub 下载任何源码；声明为 `origin` 或未设置的主机（含 ci-m2 与 GitHub-hosted）
行为逐字节不变。service 路径任何失败一律非零，不加重试、不回退 origin。
`tests/test_gate_source.py` 用真实 git 夹具（上游仓 + 合成 merge 提交 + 镜像裸仓 +
契约一致的假客户端 + argv 记录 wrapper）跑工作流里真正执行的那段 bash，
全量 1305 passed。

**关键决策与已否决方案**：协议只实现一次（`scripts/gate_source.py`），
工作流级 `GATE_SOURCE_DECIDE_SCRIPT` / `GATE_SOURCE_BOOTSTRAP_SCRIPT` 两段 bash 供
10 个调用点复用，不再各写一份；caller checkout 用「prime step 输出 mode →
`actions/checkout` 加 `if` 守卫」而不是把两种模式塞进同一步。客户端一律在取读锁之前
调用（宿主写者要排他锁），判据是假客户端自己探测排他锁能否取得，实测把锁提前会让
`test_consumer_never_calls_the_under_the_read_lock` 红。已否决：service 失败回退
origin、保留「命中也 origin depth-1 fetch」、以客户端文件存在判断主机能力、
每个调用点各写一份实现。

**下一步唯一动作**：主脑按本目录同批报告逐条复核 checkout 副作用审计清单，
并在 gate-hub W3b-1 的 `SOURCE-MODE` 落到全部主机后以 merge commit 合并。
### 2026-10-03 · 修复轮 1：凭据持久化与文档契约

**当前阶段**：implementing；R1（文档与代码矛盾）与 R2（service 路径恢复
`actions/checkout` 凭据持久化）已完成并提交。

**本段结论**：替代 `actions/checkout` 的 5 处（gate-v2 quality/primary/ocr、
gate-shadow-v2 classify/shadow）现在按该 action 默认 `persist-credentials: true`
的语义，在工作区 `.git/config` 写入
`http.<server>/.extraheader = AUTHORIZATION: basic base64("x-access-token:<token>")`，
令牌以占位符先写后原地替换，不进 argv、不进遥测与 stderr；10 处 workflow_sha
工具自举维持原样不写。设计文档中「提前合并也安全」的说法已删除，改为：env 已注入而
`SOURCE-MODE` 不可读的主机一律 `SOURCE-MODE-UNREADABLE` 非零失败，本 PR 必须在
gate-hub W3b-1 部署到全部自托管主机之后才能合并。全量 1309 passed。

**关键决策与已否决方案**：按 pinned commit `11d5960a` 的源码逐字对齐键名与值格式
（`git-auth-helper.ts` 的 `http.${serverUrl.origin}/.extraheader` +
`AUTHORIZATION: basic <base64(x-access-token:token)>`），并照抄其「占位符 + 改写
config 文件」的手法，理由是该 action 明确以此避免令牌进入进程创建审计。已否决：把令牌
直接作为 `git config` 的 argv 传入；在工具自举目录也写凭据（等于扩大既有权限面）；
补 job 级 `post:` 步骤清理（超出本轮边界且在本仓无先例，见报告「与卡面的偏差」）。

**下一步唯一动作**：主脑验收后在 gate-hub W3b-1 部署完成的前提下以 merge commit 合并
PR #276；凭据清理时机（action 的 post 步骤）是否需要跟进，另开一轮决定。

### 2026-10-03 · 修复轮 2：声明按字节判定 + 单一 deadline

**当前阶段**：implementing；F1（bash 判定吞尾部换行）、F2（锁等待重置预算）已完成并提交。

**本段结论**：`GATE_SOURCE_DECIDE_SCRIPT` 现在把声明文件读进一个哨兵字符后面再比对，
只有恰好 `origin\n` / `service\n` 通过，多余空行、缺尾换行、CRLF、前后空格一律
`SOURCE-MODE-INVALID`；`scripts/gate_source.py` 侧改为**读字节**并用同一判据
（此前 `read_text` 的通用换行会把 CRLF 规整成合法）。锁等待与镜像 fetch/show 现在共享
客户端拿到的那个 epoch deadline，不再各自领一份满预算。顺带修掉一个此前没人注意的
时钟错误：`deadline` 用 `time.monotonic()` 构造、却拿去和 `time.time()` 相减，
等于交给客户端一个「开机秒数」的 `--deadline-epoch`，且每步 timeout 都被压成 1 秒。
全量 1330 passed。

**关键决策与已否决方案**：F2 的 Python 侧没有走「把满预算传给锁」，而是把绝对 deadline
贯穿 `checkout → fetch_demand_ref → shared_consume_lock`，并新增 `left()/remaining()`
两个小函数（`remaining` 在剩余 ≤0 时 fail loud），理由是「每步自己算剩余」正是这次出错的
形状。已否决：用 `cmp` 判字节（多依赖一个 coreutils 工具，纯 bash 哨兵法即可且与镜像上的
最小假设一致）；给 job 级 `post:` 凭据清理——主脑已裁定**不修**，理由记此：`GITHUB_TOKEN`
在作业结束时即失效，且 service 路径每次重建 `.git`，残留的 extraheader 没有可用的令牌。

**下一步唯一动作**：主脑在 gate-hub W3b-1 部署完成的前提下以 merge commit 合并 PR #276。

### 2026-10-03 · 修复轮 3：校验客户端回执与需求引用

**当前阶段**：implementing；shell 自举与 Python 取码路径均校验客户端回执字段和镜像需求引用，真实 Git 夹具新增失配用例。

**本段结论**：三份工作流先校验唯一协议行的 JSON、请求仓库与 SHA、按退出码匹配的状态和 `hit/cold` 来源；镜像锁内再确认 `refs/demand/<sha>^{commit}` 精确解析为请求 SHA。Python 侧使用同一判据，拒绝失配回执或指向别的提交的引用。

**关键决策与已否决方案**：客户端成功（退出码 0）必须回 `ready`，失败退出必须回 `failed`，以保留宿主协议的 `SOURCE-*` 失败语义；允许 stderr 中存在非协议进度行，但必须恰有一行协议前缀。已否决：仅检查 marker 存在后直接执行镜像中的工具文件。

**下一步唯一动作**：运行本仓规定的全量测试与 pin 检查，随后提交并推送修复轮 3。

### 2026-10-03 · 修复轮 4：service 工作区清理 + 内部标记 fail-closed

**当前阶段**：implementing；F4（service caller checkout 清理上一作业残留）、
F5（`gate_bounded_retry.py` 内部模式标记缺失时静默回落 origin）已完成并提交。

**本段结论**：service 路径物化 caller checkout 前先清空工作区全部内容
（等价替代掉的 `actions/checkout` 默认 `clean: true`）：未跟踪、被忽略、只读
残留与符号链接全部移除，链接只 unlink 不跟随，指向 dest 外的目标完好；非工作区根
dest（`_gate-action-src` 等）沿用整目录重建，同样无残留，两类都有真实 git 夹具
用例跑工作流抽出的真实 bash 锁住。`declared_source_mode()` 在
`GATE_HUB_GIT_MIRROR_DIR` 已设置而 `$RUNNER_TEMP/gate-source-mode` 缺失 /
不可读 / 非法时以 `SOURCE-MODE-UNREADABLE` / `SOURCE-MODE-INVALID` 非零失败
（与 `gate_source.py` 同一词汇表，由测试锁定相等），只有 env 未设置/空或标记恰为
`origin` 才走 origin；service 环境删标记跑 `gate_bounded_retry.py checkout` 非零且
零 git/curl argv。全量 1356 passed。

**关键决策与已否决方案**：F4 选「物化前清空 dest」而非「checkout 后
`git clean -ffdx`」：不依赖 git 对嵌套仓/只读目录的清理语义，清理由 Python 一次
iterdir 完成（目录且非符号链接 → rmtree，其余 → unlink），且清理不占取码预算；
F5 判据读字节、恰 `origin\n`/`service\n`，与工作流 bash 判定同形。已否决：标记
缺失回退 origin（正是本卡要消灭的静默出错）。顺带把修复轮 2 引入的
`test_python_lock_waits_only_on_what_is_left_of_the_budget` 上界从 5.0 放到 5.5：
锁循环 50ms 轮询会让 deadline 检测最多晚一个间隔，基线 commit 加 CPU 负载实测
5.14s 超 5.0（与本轮改动无关），5.5 保留余量且不影响该测试本来的锁语义判别
（判别的红验由 `test_python_lock_timeout_stays_inside_one_budget` 承担）。

**下一步唯一动作**：主脑验收后以 merge commit 合并 PR #276（前置：gate-hub W3b-1
的 `SOURCE-MODE` 部署到全部自托管主机）。

### 2026-10-03 · 收窄轮：撤回 caller checkout 替换，只留工具自举与 action 补拉

**当前阶段**：implementing；按主脑决定（Opus/Codex 咨询结论：替换 `actions/checkout`
是开放式规格、评审无法收敛，拆 PR 止血）撤回本 PR 的 caller checkout service 替换，
保留工具自举与 preflight/advisory 补拉。

**本段结论**：5 处 caller checkout（gate-v2 quality/primary/ocr、shadow
classify/shadow）的 prime step、`actions/checkout`、字节统计 step 恢复为与
origin/main 76ce334 逐字节相等（新契约测试 `tests/test_caller_checkout_baseline.py`
从 fixture 基准字节锁住，加回 mode 守卫即红）；`GATE_CHECKOUT_MIRROR_SCRIPT` 恢复
基准原文，service 序言删除。`gate_source.py` 删除仅服务 caller checkout 的代码
（工作区根清空、凭据持久化、origin remote 设置及相关字面量/常量），`checkout()`
只负责非工作区根 dest 的整目录重建物化，PATH 缺失或非子目录即 fail loud。
新增浅仓交互测试：depth-1 浅克隆（HEAD 为合成 merge 提交）下 advisory 经服务补拉
base 后 `git diff base head` 与上游一致、零网络 argv。全量测试绿。

**关键决策与已否决方案**：caller checkout 改走宿主服务的方案改为另开卡做
（「保留 actions/checkout、step 级 URL 改写到作业私有临时仓」，见
`/home/zlx/.local/state/consult/git-auto-cache-w3a-convergence/{opus,codex}.md`），
不在本 PR。已否决：在本 PR 内继续补 `actions/checkout` 等价契约（咨询结论：
没有成文契约时「做完」无定义，评审永远能再找到一条）。夹具上游补推
`refs/heads/base`，让假客户端能为「PR 基线」SHA 供数（此前只有分支尖端可服务）。

**下一步唯一动作**：主脑验收后以 merge commit 合并 PR #276（前置：gate-hub W3b-1
的 `SOURCE-MODE` 部署到全部自托管主机）；caller checkout 取码另开卡实施。
