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