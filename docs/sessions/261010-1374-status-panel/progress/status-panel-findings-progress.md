# status-panel primary findings 进度

## 里程碑 1：实现与正例测试
- 当前阶段：正例实现完成，反例待补。
- 本段结论：status-panel 现在只在当前状态为 fail 且 canonical audit verdict 为 fail 时渲染 finding 摘要；正例经生产形态 `--publish-only` 到 PR 评论 body 验证，脱敏、单条限长和 50 条上限均通过。
- 关键决策与已否决方案：复用 `_finding_summary_rows` 的 50 条 / 200 字符上限和既有发布脱敏，不暴露 audit 其它字段；新增辅助函数仅供 PR 面板投影使用。主审 job 原有 `PRIMARY-FINDINGS-SUMMARY-*` 字节输出保持不变，复用唯一 marker 常量拼装旧 marker，满足单一失败可见性定位。

  ```python
  def _primary_findings_panel_lines(primary_audit: Any) -> list[str]:
  def _publish_only_panel_body(
      monkeypatch: pytest.MonkeyPatch,
      tmp_path: Path,
      audit_record: dict[str, object],
      *,
      remove_audit_before_publish: bool = False,
  ) -> tuple[str, dict[str, object]]:
  ```

- 下一步唯一动作：补 pass audit 与 publish 时 audit 缺失两条反例并运行聚合器测试。
