### 2026-10-03 · 预热段取锁前先请宿主准备目标提交（W3c 实施）

**当前阶段**：implementing；工作流、测试、文档完成并已提交，等待主脑验收与合并。

**本段结论**：计费主机上 101/184 次预热是 `reason=mirror-repo-missing`（镜像里根本没
这个仓），所以 `service` 主机上的 `GATE_CHECKOUT_MIRROR_SCRIPT` 现在在 `mirror_root`
判定之后、取 `consume.lock` 之前先调宿主客户端把 `$sha` 准备进镜像；夹具实测预热
`hit=1`、后续 `actions/checkout` 等价 fetch 的 pack 增量低于「无准备」基线的 10%，
客户端失败则非零带 `SOURCE-*` 且不 fetch origin。`actions/checkout` 步骤与 5 处
step 组与 origin/main 基线仍逐字节相等（`tests/test_caller_checkout_baseline.py`
未改即绿）。全量 1360 passed。

**关键决策与已否决方案**：客户端调用与回复校验只实现一次——抽成工作流级
`GATE_SOURCE_PREPARE_SCRIPT`（`gate_source_prepare()`），工具自举与预热段两处
`eval` 后调用，不再写第五份；`prepare` 遥测从客户端那唯一一行协议回复里读，不另立
解析器。准备阶段是**硬失败**（`SOURCE-*` 打红），与预热段其它软 miss 不同，故该段
期间摘掉 EXIT trap，防止 `consumer-error` 把硬失败洗成 exit 0。已否决：把准备失败
降级回 origin（与已部署的 SOURCE-MODE 判定表不一致）、为预热段另写一份客户端校验、
改 `gate-v2-disposition.yml`（本卡禁改文件；它仍内联同一段调用，测试按字节锁住两者
不漂移）、用「镜像里没有该仓」判断是否调客户端（应听主机声明，不听现场状态）。

**下一步唯一动作**：主脑按 merge commit 合并本卡，并在 canary 绿后确认 `refs/tags/v2`
已含本卡 SHA（release 完成条件由主脑关单时核对）。