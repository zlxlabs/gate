# gate#110 进度存档 — aggregator stale-draft 载荷复核

## 里程碑 1：DESIGN-note 入库

- 当前阶段：design 落盘完成，准备写红测。
- 本段结论：`design-gate.md` 已按字节原样复制为 `docs/sessions/260902-gate-triage/design.md`（diff 校验一致）。下游消费者核查完成：gate-hub `scripts/review` 与 `tests` 中所有 `reason_code` 命中均属 shadow/ocr 状态域，无任何 gate-terminal reason 枚举/白名单；本仓内唯一域校验在 `aggregate.py` 自身的 `TERMINAL_REASON_DOMAIN` 元组，扩元组即覆盖。
- 关键决策与已否决方案：新 reason code 复用 `review_unavailable` classification（锁定决策）；`pr_draft_now=None` 在 `is_draft and primary skipped` 分支语义为「复核失败」fail-closed——这会让既有 `(skipped, is_draft=True)` 测试格与 CLI 用例变红，须改为显式传 `pr_draft_now=True` / monkeypatch `_fetch_pr_draft`（语义必然，将在报告写明）。
- 下一步唯一动作：写判定矩阵新格、`_fetch_pr_draft` 单测、CLI 测试（红）。
