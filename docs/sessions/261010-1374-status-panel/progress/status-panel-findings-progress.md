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

## 里程碑 2：反例与失败可见性测试
- 当前阶段：反例测试完成，待全量验证。
- 本段结论：pass audit（含 finding）与 `--publish-only` 时缺失 audit 均不渲染 findings，PR 评论 body 与无 audit 的既有投影逐字相同；fail audit 结构异常会以 `ValueError` fail loud。聚合器窄测 376 项全部通过。
- 关键决策与已否决方案：不为 audit 缺失增加 fallback 文案或重解析日志；有效 audit 由既有发布路径注入，结构异常不吞错。
- 下一步唯一动作：运行仓库全量验证命令并据结果提交最终里程碑。

## 里程碑 3：全量验证
- 当前阶段：实现、正反例及全量验证均完成，待推送分支。
- 本段结论：全量命令 1430 项通过，聚合器窄测 376 项通过，`PRIMARY-FINDINGS` 源码标记计数为 1。故意禁用渲染分支后正例测试以 `AssertionError` 转红，随后只还原该条件行。
- 关键决策与已否决方案：主干基线不可用，继承红无法判定；按 personal 仓规则不以本地旧基线推断 CI 结果。
- 下一步唯一动作：提交本进度段并推送本卡分支，核实远端分支头。
