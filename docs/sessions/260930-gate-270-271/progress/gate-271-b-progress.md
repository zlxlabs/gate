## 里程碑 1：失败可诊断

- 当前阶段：implementing（失败诊断层完成）
- 本段结论：发布失败的回执加上操作、尝试数和异常类型；Actions warning 输出稳定的 `PANEL_PUBLISH_FAILED` 字面量。Step Summary 标明面板可能仍显示旧状态，`--publish-only` 失败仍返回 0。
- 关键决策与已否决方案：异常原因复用 `scrub_outbound.py` 脱敏，并先删除完整 URL query；不改 workflow、裁决与退出码。
- 下一步唯一动作：实现 COMMENT_LOOKUP 与 PATCH 的有界重试，并在 PATCH 结果不明时 GET 回读。
