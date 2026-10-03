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
