# D 卡进度存档：v2 抬升前在真实自托管 runner 上验证候选版本的取码失败路径

## 里程碑 1：探针脚本与判据测试
- 当前阶段：implementing
- 本段结论：新增 scripts/v2_source_failure_probe.sh（镜像目录/宿主客户端/片段提取三道 fail-loud 检查，各自 SOURCE-PROBE-* 字面量；PyYAML 提取候选树内 gate-v2.yml 的 GATE_SOURCE_PREPARE_SCRIPT，对不存在 commit `000...01` 调宿主客户端，断言 `SOURCE-CLIENT-FAILED: exit=` 存在、协议行带 SOURCE- 前缀真实 code、无 SOURCE-CLIENT-CONTRACT）；判据测试 10 条，旧片段（gate d88d62cd）原文内嵌为 fixture，假客户端只测判据。三条判据各做单判据注入红验，均转红（AssertionError）。
- 关键决策与已否决方案：判据测试不用 `git show d88d62cd` 取旧片段——CI 浅 clone（无 fetch-depth: 0）拿不到历史对象，改原文内嵌；探针把捕获的片段输出透传到 stderr——command substitution 会吞掉 `::error::` 注解，不透传则 runner 上失败时无从排障；d88d62cd 旧片段对真实 producer 失败行（status:"error"）先撞 CONTRACT 校验退出，天然满足「修复前必失败」。
- 下一步唯一动作：写 v2-tag-sync 结构测试（红）。

## 里程碑 2：workflow 结构调整（select → probe → publish）
- 当前阶段：implementing
- 本段结论：v2-tag-sync 拆为 sync（选目标 + caller contract + outcomes 转发）→ failure-probe（needs sync、self-hosted [linux, ci]、checkout ref=target_sha 浅层、跑候选树自带探针脚本）→ publish（needs 双 job、Move 条件 `move=='true' && needs['failure-probe'].result=='success'`、Report 七状态新增 failure_probe_failed、Lag 条件改为 needs.sync.outputs.guard_outcome）。结构测试 5 条先行红（KeyError: publish）后转绿，move-if 红验（删 probe 条件）转红后还原；全量 1481 绿，check_pinned_uses OK。
- 关键决策与已否决方案：新 job id 用连字符 `failure-probe`，表达式走 `needs['failure-probe'].result` 索引语法（点号语法不认连字符）；Report 语义保持不变，仅新增 failure_probe_failed 一态——不加则探针红会误报成 query_failed，掩盖「验证不过」与「查询坏了」的区别（单消费者但必要：状态是 v2 停摆排障的唯一人读出口）。
- 下一步唯一动作：提交后由主脑合并，用 workflow_dispatch 在真实 runner 取证。
