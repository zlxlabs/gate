## 里程碑 1：聚合器定因与标记

- 当前阶段：implementing
- 本段结论：聚合器按 draft、fork、hosted_runner、review_exempt 顺序识别跳过原因；无法定因的 primary skip 进入 `integration_error / unexpected_primary_skip`。聚合器把单行 V1 标记写入 stdout 和 Step Summary，并把 `skip_reason` 写入 terminal envelope。
- 关键决策与已否决方案：`skip_reason` 存在 `Outcome` 上，供标记、Step Summary 和 terminal envelope 共用。未知原因的真实跳过必须保留 `primary=skipped, skip_reason=null`，这与卡面“primary=executed 等价于 skip_reason=null”的通用断言冲突；本实现遵循负例和实际执行事实。
- 下一步唯一动作：通过 gate workflow 传入 fork 与 classify 原始输出，并增加同源条件契约测试。

## 里程碑 2：workflow 传参与契约测试

- 当前阶段：implementing
- 本段结论：`Aggregate required verdict` 现在传入 draft、fork、runner 和 classify 原始输出；REVIEW_EXPECTED、primary `if:` 及其判定顺序保持不变。契约测试逐项锁住输入表达式与 CLI 参数，producer 子进程测试还锁住紧凑排序 JSON 的实际 stdout 字节。
- 关键决策与已否决方案：fork 事实由 head 仓与当前仓比较后作为布尔值传入；聚合器不调用 GitHub API 推导。
- 下一步唯一动作：补齐设计契约文档并追加进度后跑全量验收。

## 里程碑 3：设计契约与收尾验证

- 当前阶段：implementing
- 本段结论：设计文档记录了标记字段、有限值域、判定顺序和消费规则，也明确说明了 fail-closed 负例中 `primary=skipped, skip_reason=null` 的必要例外。实现与契约测试已提交，正在跑最终全量验证。
- 关键决策与已否决方案：不伪报未执行的 primary 为 `executed`；负例保持真实执行状态并以非零退出和非 `skipped` 的 gate_result 失败。
- 下一步唯一动作：跑卡面要求的全量测试、相关文件测试、pin 检查并保存完整报告。

## 补充里程碑：保持无跳过调用方可用

- 当前阶段：implementing
- 本段结论：全量测试发现仓内另有聚合器子进程用例只覆盖 primary 执行路径，未传新增参数。CLI 现在对缺失的 fork/classify 输入使用不能生成 skip reason 的默认值；已执行 primary 仍可处理，缺事实的 skipped 仍 fail closed。
- 关键决策与已否决方案：不扩写卡面未授权的 convergence 测试文件；通过 CLI 的 fail-closed 缺省值兼容只处理执行结果的调用方。
- 下一步唯一动作：重跑全量测试及相关契约检查并生成交付报告。

## 文档勘误：值域顺序与判定优先级

- 当前阶段：implementing
- 本段结论：复核发现设计文档曾把 skip reason 值域的列举顺序误称为判定优先级；现已改为分别描述值域和 draft→fork→hosted_runner→review_exempt 的判定顺序。
- 关键决策与已否决方案：无。
- 下一步唯一动作：提交并推送文档勘误，更新最终交接与报告。

## 收尾验收

- 当前阶段：complete
- 本段结论：全量测试 1196 项与相关文件复验 742 项通过；workflow pin 检查通过，actionlint 忽略基线已有 SC2129 后通过。分支已推送，producer 子进程断言 stdout 恰有一条排序紧凑的 V1 标记。
- 关键决策与已否决方案：无原因的真实 skipped 继续报告 `primary=skipped, skip_reason=null` 并 fail closed；不伪报 primary 已执行。
- 下一步唯一动作：主脑审查并合并 workflow PR，待 v2 canary 自动抬升后核实真实纯文档 PR 的 gate 日志。
