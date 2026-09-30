## 里程碑 1：失败可诊断

- 当前阶段：implementing（失败诊断层完成）
- 本段结论：发布失败的回执加上操作、尝试数和异常类型；Actions warning 输出稳定的 `PANEL_PUBLISH_FAILED` 字面量。Step Summary 标明面板可能仍显示旧状态，`--publish-only` 失败仍返回 0。
- 关键决策与已否决方案：异常原因复用 `scrub_outbound.py` 脱敏，并先删除完整 URL query；不改 workflow、裁决与退出码。
- 下一步唯一动作：实现 COMMENT_LOOKUP 与 PATCH 的有界重试，并在 PATCH 结果不明时 GET 回读。

## 里程碑 2：有界重试与结果回读

- 当前阶段：implementing（重试与回读完成）
- 本段结论：COMMENT_LOOKUP 与 PATCH 仅重试暂时性网络异常及 5xx，最多三次、固定间隔两秒；PATCH 失败先 GET 指定评论并按 `(run_id, run_attempt)` 验证是否已写入。目标单测已通过，`--publish-only` 仍返回 0。
- 关键决策与已否决方案：PATCH 回读失败占用尝试次数；4xx 和 POST 不重试；共用一个有界重试函数。
- 下一步唯一动作：补齐失败处置文档并完成收尾验收。
