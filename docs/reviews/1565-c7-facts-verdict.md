# C7 上轮事实传递独立审查

- 审查对象：`15f984a81ed184e64fb36d99f171770bf4b05a80..358a0caf7f5055c942e62f06222cd3d0970773bb`
- 风险等级：`personal`
- 结论：`fail`；1 条 P2，阻断交付。
failure-visibility: p2-only

## Finding

### P2 · 阻断：新增的顶层 `head_sha` 会被 review-primary 消费端拒绝

**对应不变式：** 上轮机器事实经 renderer 输出文件后，必须能被真实消费者读取；可选 `head_sha` 作为机器事实传递，不得破坏 review 提示词路径和原有处置权限。

**实际触发：** 新 renderer 在 `available=true` 时写入同条 ledger 顶层 `head_sha`，缺值时写 `null`；降级 payload 也包含 `head_sha: null`。工作流在 JSON 文件非空时把路径传给 `REVIEW_PREVIOUS_ROUND_PATH`。gate-hub 当前消费实现 `scripts/review/previous_round_context.py` 的 `_TOP_LEVEL_KEYS` 仍是四键集合，`_check_exact_keys` 会拒绝额外的 `head_sha`。`review-primary` 把该契约错误转成 `SetupError`，在生成提示词前以 `EXIT_SETUP_ERROR`（3）退出。

**后果与阻断：** 只要注入步骤成功生成文件，review-primary 就无法读取上轮上下文并不会启动主审；即使上轮 head 缺失，文件仍带 `head_sha: null`，同样触发。此为可见的 fail-fast，判 P2；但主审门禁无法完成，阻断本次交付。该 finding 依据消费端代码和调用路径，不依据已知过时的四键文档。

**证据位置：**

- gate renderer：`.github/actions/gate-aggregator/aggregate.py` 的 `load_previous_round_findings` 与 `_render_previous_context_cli`。
- gate workflow：`.github/workflows/gate-v2.yml` 的 “Inject previous-round findings into primary review context” 步骤。
- 消费端：`gate-hub/scripts/review/previous_round_context.py:10,31-38,53-55`；调用及错误转译见 `gate-hub/scripts/review/review-primary:1332-1338,1465-1475`。

## 已核对的不变式

- 31 项输入的第 31 条及原始 `quote`、`anchor` 由 renderer 的真实 CLI 写到输出文件，再经 loader 到关系消费者；尾部同 id finding 被判为 `repeat`。新增测试使用仓内 ledger fixture 与窄 Silo mock，不是手造 loader payload。
- `head_sha` 只取 ledger 顶层；缺失时写入并回读 `null`，没有从当前 head 或嵌套 identity 猜测。
- 同 run 较早 attempt 可用；当前 attempt 与未来 attempt 降级且日志可见。PR 归属及 `(run_id, run_attempt)` 先后比较保留。
- 关系计算已消费完整 previous findings 列表，31 条之后的旧 finding 不会因 30 条截断而被标为 `new`。
- disposition 的 30 条投影限制、处置字段与 gate 端处置处理未变；gate-hub 的 6000 字符提示预算和渲染字段未变。新增顶层字段在进入渲染前即触发严格契约错误，见上述阻断 finding。

## 核查命令与限制

- 静态核对：`git diff --no-ext-diff --unified=80 15f984a81ed184e64fb36d99f171770bf4b05a80..358a0caf7f5055c942e62f06222cd3d0970773bb --`；`git show 358a0caf7f5055c942e62f06222cd3d0970773bb:.github/workflows/gate-v2.yml`；`git grep` 核对 producer、loader、关系消费者与字段调用点；只读检查 gate-hub 消费端源码。
- 未运行 pytest 或 OCR；按卡要求未调用真实 Silo、模型或部署环境。测试链路结论来自对新增测试与生产函数调用的静态审查，未在本轮执行。
- 本 verdict 只审查 C7 的事实输入改动，不实现父单要求的关闭/继承规则，也不代表父卡 C7 已完成。
