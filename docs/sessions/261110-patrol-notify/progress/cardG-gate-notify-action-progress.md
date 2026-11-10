# cardG-gate-notify-action 进度存档

## 段 1 — 现场核对与取源方式确定

- **当前阶段**：implementing（写码前设计落地）
- **本段结论**：worktree 干净、HEAD=base(a6b6b423)；notify job 位于 gate-v2.yml:3765-3843，
  内联飞书卡在 :3800-3818。取源采用与 gate job 同款 `job.workflow_repository`/`job.workflow_sha`
  检出到 `_gate-notify-src`（GATE_SOURCE_BOOTSTRAP_SCRIPT 是 workflow 级 env，notify job 可直接用；
  gate_bounded_retry 支持 GATE_CHECKOUT_SPARSE）。契约测试现状：notify job 有 4 个文本断言测试，
  其中 2 个锁定内联 Python 文本、将随实现迁移改写。
- **关键决策与已否决方案**：
  - 选 `_gate-notify-src` 新路径而非复用 `_gate-aggregator-src` 名：不同 job 不共享 workspace，
    复用名只会造成语义混淆，无收益。
  - notify payload 字段组装（PR 标题兜底、body 四行、links JSON）放在 notify job 的前置
    `Resolve notify payload fields` 步骤（python heredoc → GITHUB_OUTPUT），不放 action：
    action 是通用组件，不感知 PR 语义。
  - 测试的跨进程边界方案：wrapper（`python3 -c` 内联）patch `http.client.HTTPSConnection.connect`
    指向本地 HTTP 服务器，URL 仍是真实企微/飞书主机名（白名单校验全真），请求经真实 urllib/HTTP
    栈出站、被本地服务器按字节捕获。已否决：proxy 环境变量（https CONNECT 本地服务器不支持）、
    TLS 自签证书（标准库造不出证书）、假 urlopen（绕过请求构造，不满足「实际发出的字节」）。
  - notify.py 的 env 变量名前缀 `NOTIFY_`（NOTIFY_WEBHOOK 等），与旧 FEISHU_* 无耦合。
- **下一步唯一动作**：写 `.github/actions/notify/notify.py` 与 `action.yml`。

## 段 2 — 实现与验证完成

- **当前阶段**：implementing → 待提交推送
- **本段结论**：notify.py + action.yml + gate-v2 notify job（取源/组装/uses 三步）+ 新旧测试完成；
  Narrow-Verify 257 passed（三测试文件）。契约测试跟随：内联文本断言两枚退役
  （test_notify_validates_feishu_business_response 改写为结构断言、
  test_notify_rejects_non_integer_zero_response_codes 退役并由 action 测试的
  errcode=false / code=0.0 参数化用例接棒）；workflow_sha checkout 站点计数 10→11。
- **关键决策与已否决方案**：
  - 飞书等价用「序列化字节相等」锁（json.dumps 默认参数同 base），不只 dict 相等。
  - severity 映射 P0/P1→icon+template 由 SEVERITY_STYLES 单表承载；非法 severity
    fail fast（notify-invalid-severity），不做 fallback。
  - 首轮本机验证暴露两处测试缺陷已修：飞书测试未把服务器响应设为 {"code":0} 形状；
    checkout 站点计数断言未跟随新增站点。
- **下一步唯一动作**：commit + push + 开 draft PR。
