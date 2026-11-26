"""Pure Markdown and findings rendering for the gate aggregator."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

from scripts.scrub_outbound import runtime_values_from_environment, scrub_for_publish

from gate_aggregate_models import (  # noqa: E402
    ACTIONS_BOT_ID, ACTIONS_BOT_LOGIN, CALLER_CHECKS_DOMAIN,
    CONVERGENCE_ENVELOPE_KIND, CONVERGENCE_ENVELOPE_SCHEMA_VERSION,
    DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS, DEFAULT_PUBLISH_BUDGET_SECONDS,
    DISPOSITION_ARTIFACT_PREFIX, FINDING_SUMMARY_MAX_FINDINGS,
    FINDING_SUMMARY_MAX_TITLE_CHARS, FINDING_SUMMARY_UNAVAILABLE,
    GATE_RESULT_DOMAIN, GITHUB_API_TIMEOUT_SECONDS,
    HISTORY_RECONSTRUCTION_BUDGET_ENV, IDENTITY_QUINTUPLE,
    MAX_HISTORY_WARNING_CHARS, MAX_REPO_WIDE_HISTORY_PAGES, MAX_TARGETED_HISTORY_RUNS,
    PANEL_BUCKET_BY_GATE_RESULT, PANEL_DELIVERY_KIND, PANEL_DELIVERY_SCHEMA_VERSION,
    PANEL_HISTORY_ROW_SCHEMA_VERSION, PANEL_MARKER, PREFLIGHT_RESULT_DOMAIN,
    PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE, PRIMARY_BUDGET_EXHAUSTED_REASON,
    PRIMARY_FINDINGS_PANEL_MARKER, PRIMARY_RESULT_DOMAIN,
    PRIMARY_SKIP_REASON_DOMAIN, PRIMARY_VERDICT_DOMAIN,
    PUBLISH_BUDGET_ENV, PUBLISH_OPERATION_ORDER, QUALITY_RESULT_DOMAIN,
    RUNNER_DOMAIN, SILO_CLI_CALL_TIMEOUT_SECONDS, SILO_DISPOSITION_TIER,
    SILO_TERMINAL_TIER, SYNTHETIC_STATUS_ARTIFACT_MISSING,
    SYNTHETIC_STATUS_TIMED_OUT, TERMINAL_CLASSIFICATION_DOMAIN,
    TERMINAL_HISTORY_CACHE_SCHEMA_VERSION, TERMINAL_PRIMARY_VERDICT_DOMAIN,
    TERMINAL_REASON_DOMAIN, _ACTIVE_PUBLISH_BUDGET, _COLON_RUN,
    _FINDING_SUMMARY_COLUMNS, _IDENTITY_INT_FIELDS, BoolParseError, HistoryLoad,
    Identity, Outcome, _PublishBudget, _PublishBudgetExhausted, as_bool,
    _is_strict_int,
)

REASON_CODE_EXPLANATIONS = {
    "disposition_resolved": (
        "The primary audit reported findings, but every current P1 is covered by an active, "
        "evidence-bound gate-disposition receipt for this exact head and audit."
    ),
    "primary_findings": (
        "The primary reviewer REJECTED this change — the specific findings are in the primary "
        "review result for this run (linked above); the Problems list below only mirrors the verdict."
    ),
    "audit_missing": (
        "The primary reviewer's conclusion could NOT be read for this run (no audit artifact was "
        "produced); the reason for the primary job result is unknown. This is not a reviewer rejection."
    ),
    "audit_invalid": (
        "The primary reviewer's conclusion could NOT be read for this run (the audit artifact failed "
        "validation); the reason for the primary job result is unknown. This is not a reviewer rejection."
    ),
    "audit_source_mismatch": (
        "The primary reviewer's conclusion could NOT be read for this run (the audit's source attempt "
        "did not match the selected artifact); the reason for the primary job result is unknown. "
        "This is not a reviewer rejection."
    ),
    "job_audit_mismatch": (
        "The primary reviewer's recorded conclusion contradicts the primary job's own status — "
        "the gate fails closed. Check the primary job logs."
    ),
    "unexpected_primary_skip": (
        "The primary review should have run but was skipped — this is NOT the normal draft/fork "
        "skip. Check the primary job configuration."
    ),
    "quality_infra": (
        "The quality job failed but its business checks show no failure (or the evidence was "
        "never recorded) — this is an infrastructure problem, not a code problem. Check the "
        "quality job's control-plane steps (checkout, cache, MagicDNS, ledger upload)."
    ),
}

class _FindingsSummaryUnavailable(Exception):
    """One degraded finding-summary render; the message is the reason token
    printed after `FINDING_SUMMARY_UNAVAILABLE reason=`."""

def _summary_cell(value: Any) -> str:
    """Flatten one finding field into a single line both surfaces can carry.

    Every whitespace run (newline, tab, CR) collapses to one space, so a title
    can never occupy a line of its own; a run of colons collapses to one, so
    the `::` workflow-command prefix cannot survive at all; and the text is
    length-capped so one finding cannot flood the log.
    """
    flat = "" if value is None else " ".join(str(value).split())
    flat = _COLON_RUN.sub(":", flat)
    if len(flat) > FINDING_SUMMARY_MAX_TITLE_CHARS:
        flat = flat[: FINDING_SUMMARY_MAX_TITLE_CHARS - 1] + "…"
    return flat

def _markdown_cell(value: str) -> str:
    """Escape the one character that would break a markdown table row."""
    return value.replace("|", "\\|")

def _append_step_summary(summary_path: Optional[str], text: str) -> None:
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(text)

def _degrade_findings_summary(reason: str, summary_path: Optional[str]) -> None:
    """Announce one degraded render on both surfaces.

    The reason vocabulary is code-owned (`audit-missing`, `audit-unparseable`,
    `findings-field-missing`, `render-failed`), never audit content, so this
    line needs no scrub. An absent audit is NOT the same claim as "the
    reviewer found nothing" and must never be rendered as an empty table.
    """
    line = f"{FINDING_SUMMARY_UNAVAILABLE} reason={reason}"
    print(line)
    _append_step_summary(summary_path, f"## primary review findings\n\n{line}\n")

def _load_audit_findings(audit_path: Path) -> tuple[list[Any], str]:
    """(findings, verdict) from a canonical primary audit, or one degrade reason.

    `title` is read because gate-hub's contracts._project_finding_text_aliases
    copies it onto every finding whose `issue` is non-empty, so a real audit
    always carries it alongside the schema-required `issue`.
    """
    if not audit_path.is_file():
        raise _FindingsSummaryUnavailable("audit-missing")
    try:
        text = audit_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _FindingsSummaryUnavailable(f"audit-unreadable {type(exc).__name__}") from exc
    try:
        audit = json.loads(text)
    except ValueError as exc:
        # json.JSONDecodeError and UnicodeDecodeError are both ValueErrors:
        # bytes the producer never wrote, not a file this job cannot open.
        raise _FindingsSummaryUnavailable(f"audit-unparseable {type(exc).__name__}") from exc
    if not isinstance(audit, dict):
        raise _FindingsSummaryUnavailable("audit-unparseable root-not-object")
    result = audit.get("result")
    findings = result.get("findings") if isinstance(result, dict) else None
    if not isinstance(findings, list):
        raise _FindingsSummaryUnavailable("findings-field-missing")
    return findings, str(audit.get("verdict"))

def _finding_summary_rows(findings: list[Any]) -> list[dict[str, str]]:
    rows = []
    for finding in findings:
        line = finding.get("line")
        file_name = _summary_cell(finding.get("file"))
        # `line` is model output exactly like `title` is, so it gets the same
        # treatment before it is joined: a raw one would split the markdown row
        # on `|`, start a fresh log line on a newline, and a line-leading `::`
        # would be parsed as a workflow command — the exact shape this renderer
        # exists to keep out of the log.
        location = file_name if line is None else f"{file_name}:{_summary_cell(line)}"
        rows.append({
            "severity": _summary_cell(finding.get("severity")),
            "id": _summary_cell(finding.get("id")),
            "location": location,
            "title": _summary_cell(finding.get("title")),
        })
    return rows

def _primary_findings_panel_lines(primary_audit: Any) -> list[str]:
    """Project only bounded finding labels from the validated primary audit."""
    if not isinstance(primary_audit, dict):
        raise ValueError("primary findings panel audit is not an object")
    verdict = primary_audit.get("verdict")
    if verdict not in PRIMARY_VERDICT_DOMAIN:
        raise ValueError(f"primary findings panel audit verdict is invalid: {verdict!r}")
    if verdict != "fail":
        return []
    result = primary_audit.get("result")
    if not isinstance(result, dict):
        raise ValueError("primary findings panel audit result is not an object")
    findings = result.get("findings")
    if not isinstance(findings, list):
        raise ValueError("primary findings panel audit findings is not a list")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"primary findings panel finding {index} is not an object")
        for field in ("id", "severity", "file"):
            if not isinstance(finding.get(field), str) or not finding[field]:
                raise ValueError(f"primary findings panel finding {index} has invalid {field}")
        if "line" not in finding or (
            finding["line"] is not None and not _is_strict_int(finding["line"])
        ):
            raise ValueError(f"primary findings panel finding {index} has invalid line")
        if "title" in finding and not isinstance(finding["title"], str):
            raise ValueError(f"primary findings panel finding {index} has invalid title")
    if not findings:
        return []

    shown_rows = _finding_summary_rows(findings[:FINDING_SUMMARY_MAX_FINDINGS])
    overflow = len(findings) - len(shown_rows)
    lines = [
        "",
        f"#### {PRIMARY_FINDINGS_PANEL_MARKER} — 主审发现（{len(shown_rows)} / {len(findings)} 条）",
        "",
        "| severity | id | location | title |",
        "| --- | --- | --- | --- |",
    ]
    for row in shown_rows:
        lines.append("| " + " | ".join(_markdown_cell(row[key]) for key in _FINDING_SUMMARY_COLUMNS) + " |")
    if overflow:
        lines.extend(["", f"+{overflow} more"])
    return lines

def _render_findings_summary(audit_path: Path, summary_path: Optional[str], *, _ports: Any = None) -> None:
    """Print the finding summary to stdout and append it to the Step Summary."""
    try:
        findings, verdict = _load_audit_findings(audit_path)
        shown = _finding_summary_rows(findings[:FINDING_SUMMARY_MAX_FINDINGS])
        overflow = len(findings) - len(shown)
        lines = [
            f"{PRIMARY_FINDINGS_PANEL_MARKER}-SUMMARY verdict={verdict} findings={len(findings)} shown={len(shown)}"
        ]
        for row in shown:
            lines.append(
                "PRIMARY-FINDING severity={severity} id={id} at={location} title={title}".format(**row)
            )
        if overflow:
            lines.append(f"{PRIMARY_FINDINGS_PANEL_MARKER}-SUMMARY-OVERFLOW ... and {overflow} more")
        table = [
            f"## primary review findings (verdict {verdict}, {len(shown)} of {len(findings)} shown)",
            "",
            "| severity | id | location | title |",
            "| --- | --- | --- | --- |",
        ]
        for row in shown:
            table.append("| " + " | ".join(_markdown_cell(row[key]) for key in _FINDING_SUMMARY_COLUMNS) + " |")
        if overflow:
            table += ["", f"... and {overflow} more"]
        runtime_values = _ports.runtime_values_from_environment()
        print(_ports.scrub_for_publish("\n".join(lines) + "\n", runtime_values=runtime_values), end="")
        _append_step_summary(
            summary_path,
            _ports.scrub_for_publish("\n".join(table) + "\n", runtime_values=runtime_values),
        )
    except _FindingsSummaryUnavailable as exc:
        _degrade_findings_summary(str(exc), summary_path)

def _render_findings_summary_cli(argv: list[str], *, _ports: Any = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-path", required=True)
    parser.add_argument("--summary-path", default=None, help="$GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)
    try:
        _render_findings_summary(Path(args.audit_path), args.summary_path, _ports=_ports)
    except Exception as exc:
        # Fail-open observability boundary (gate-hub#1294): this mode exists to
        # ADD readable evidence and must never change the primary job's result,
        # so anything the renderer did not already classify leaves one greppable
        # line behind and exits 0. The workflow step additionally guards its own
        # invocation with `|| echo ... reason=render-invocation-failed`.
        print(f"{FINDING_SUMMARY_UNAVAILABLE} reason=render-failed {type(exc).__name__}")
    return 0

def _action_sentence(
    outcome: Outcome, *, repository: Optional[str], identity: Optional[Identity],
    is_draft: Optional[bool], runner: Optional[str], primary_audit: Any = None,
) -> Optional[str]:
    """The one-line "what do I do now" sentence rendered immediately after
    `**Result: …**` and BEFORE the machine codes — the aggregate mail/comment
    goes to people who were not watching the run, so the human action comes
    first and `classification=`/`reason_code=` follow it (never the other way
    round). Every gate_result gets one, including pass ("no action needed").
    The run URL is built from data the aggregator already has (repository +
    identity.run_id) — deliberately no new CLI flag or env var (locked
    decision: GHES server-URL configurability is out of scope)."""
    run_url = None
    if repository and identity is not None:
        run_url = f"https://github.com/{repository}/actions/runs/{identity.run_id}"
    gate_result = outcome.gate_result
    if gate_result == "pass":
        if outcome.reason_code == "disposition_resolved":
            return "All current P1 findings are covered by active evidence-bound disposition receipts; the gate is green."
        if outcome.recorded_disposition_claims:
            return (
                "A disposition receipt claim was recorded; the gate conclusion is unchanged and green."
            )
        return "No action needed — quality passed and the primary reviewer approved this change; the gate is green."
    if gate_result == "skipped":
        if is_draft:
            return (
                "No action needed — the primary review is intentionally skipped while the PR is a draft; "
                "the full primary review will run once you mark the PR ready for review."
            )
        if runner == "hosted":
            return (
                "No action needed — the primary review only runs on runner=self and this run used "
                "runner=hosted, so the skip is expected; if you need the primary review on this PR, "
                "switch runner to self."
            )
        return "No action needed — the primary review was not expected for this PR, so the skip is accepted."
    if gate_result == "fail":
        if outcome.reason_code == "primary_findings":
            sentence = "Action needed — the primary reviewer rejected this change: read its findings, address them, and push again."
        else:
            sentence = "Action needed — the gate is red: fix the problem(s) listed under Problems below, then re-run."
        if run_url:
            sentence += f" Full details: {run_url}"
        return sentence
    if gate_result == "unavailable":
        if outcome.classification == "review_unavailable" and outcome.reason_code == "primary_unavailable":
            budget_action = _primary_budget_exhausted_action(primary_audit)
            if budget_action:
                return budget_action
        sentence = (
            "Action needed — the primary review outcome could not be determined (this is neither an "
            "approval nor a rejection): investigate the run before merging."
        )
        if run_url:
            sentence += f" Run: {run_url}"
        return sentence
    return None

def render_summary(
    outcome: Outcome, *, repository: Optional[str] = None, identity: Optional[Identity] = None,
    is_draft: Optional[bool] = None, runner: Optional[str] = None, primary_audit: Any = None,
    primary_result: Optional[str] = None,
) -> str:
    lines = ["### Required Gate v2 — aggregate verdict", ""]
    # Top line shows the four-state gate_result (pass/fail/skipped/unavailable)
    # instead of collapsing to ok -> pass|fail, so an accepted skip (draft/fork)
    # is visibly distinct from a real pass, and an unreadable primary review is
    # visibly distinct from a reviewer rejection (issues #32/#43). Outcomes from
    # the malformed-input paths carry no terminal fields — keep the legacy
    # binary rendering there.
    gate_result = outcome.gate_result or ("pass" if outcome.ok else "fail")
    lines.append(f"**Result: {gate_result}**")
    if outcome.classification is not None:
        # Human-first: the action sentence answers "what do I do now" BEFORE
        # the machine codes (never after), for every terminal gate_result.
        action = _action_sentence(
            outcome, repository=repository, identity=identity, is_draft=is_draft,
            runner=runner, primary_audit=primary_audit,
        )
        if action:
            lines.append(action)
        lines.append(
            f"Terminal state: classification=`{outcome.classification}`, "
            f"reason_code=`{outcome.reason_code}`, gate_result=`{outcome.gate_result}`"
        )
    if outcome.convergence_shadow is not None:
        shadow = outcome.convergence_shadow
        if shadow["history_status"] == "history_unavailable":
            lines.append("PR 累计主审轮（影子）：`history_unavailable`；未输出累计数，不改变 gate 结论。")
        else:
            lines.append(
                "PR 累计主审轮（影子）："
                f"`{shadow['eligible_rounds']}/{shadow['limit']}`，"
                f"decision=`{shadow['terminal_decision']}`；不改变 gate 结论。"
            )
    explanation = REASON_CODE_EXPLANATIONS.get(outcome.reason_code or "")
    if explanation:
        lines.append("")
        lines.append(explanation)
    if outcome.reason_code == "disposition_resolved":
        lines.extend([
            "",
            "当前主审 P1 已由绑定本轮审计的有效处置回执逐条覆盖；回执只覆盖列出的 finding。",
        ])
    lines.append("")
    if outcome.notes:
        lines.append("Accepted:")
        for note in outcome.notes:
            lines.append(f"- {note}")
        lines.append("")
    if outcome.recorded_disposition_claims:
        lines.append("Disposition receipts consumed:")
        for line in outcome.recorded_disposition_claims:
            lines.append(f"- {line}")
        lines.append("")
    if outcome.problems:
        lines.append("Problems:")
        for problem in outcome.problems:
            lines.append(f"- {problem}")
        lines.append("")
    if outcome.synthetic_audit is not None:
        lines.append(
            "**Synthetic audit generated** (no valid canonical primary audit was available for this run):"
        )
        lines.append("")
        lines.append(
            '`"verdict": null` below means no canonical primary review conclusion was available, '
            "and the cause cannot be determined from the available audit data. "
            "This is NOT a reviewer rejection."
        )
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(outcome.synthetic_audit, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"

def _primary_budget_exhausted_action(primary_audit: Any) -> Optional[str]:
    """Return the split-PR action only for an entirely budget-exhausted chain."""
    if not isinstance(primary_audit, dict) or primary_audit.get("verdict") != "unavailable":
        return None
    attempts = primary_audit.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return None
    if not all(
        isinstance(attempt, dict)
        and _is_strict_int(attempt.get("exit_code"))
        and attempt["exit_code"] == PRIMARY_BUDGET_EXHAUSTED_EXIT_CODE
        and attempt.get("reason") == PRIMARY_BUDGET_EXHAUSTED_REASON
        for attempt in attempts
    ):
        return None

    return "本 PR 规模超出单次评审预算，本次未能评审完。请拆成更小的增量 PR 后重试。"

def _panel_action(row: dict[str, Any]) -> str:
    """Return the recipient-facing action for one validated panel row."""
    gate_result = row["gate_result"]
    if gate_result == "unavailable":
        if row.get("classification") == "review_unavailable" and row.get("reason_code") == "primary_unavailable":
            budget_action = _primary_budget_exhausted_action(
                row.get("primary_audit")
            )
            if budget_action:
                return budget_action
    action = PANEL_BUCKET_BY_GATE_RESULT[gate_result]
    if gate_result == "skipped":
        return f"{action}（主审未跑，绿≠过审）"
    return action

def _history_reason_category(reason: str) -> str:
    http_match = re.search(r"\bHTTP\s+Error\s+(\d{3})\b", reason)
    if http_match:
        return f"制品下载失败：HTTP {http_match.group(1)}"
    if reason.startswith("expired artifact "):
        return "过期制品"
    if reason.startswith("artifact ") and " has no archive URL" in reason:
        return "制品缺少 archive URL"
    if reason.startswith("artifact history does not contain cached rows:"):
        return "制品历史缺少面板缓存行"
    if reason.startswith("artifact ") and ": " in reason:
        detail = reason.split(": ", 1)[1]
        return f"制品处理失败：{detail.split(':', 1)[0]}"
    if reason.startswith("bounded_scan:"):
        return "有界历史扫描达到上限"
    if reason.startswith("history budget exhausted"):
        return "历史重建预算耗尽"
    if reason.startswith("no terminal artifact matched"):
        return "未找到终态制品"
    return reason

def _summarize_history_reasons(reasons: list[str]) -> str:
    counts: dict[str, int] = {}
    for reason in reasons:
        category = _history_reason_category(reason)
        counts[category] = counts.get(category, 0) + 1
    return "；".join(f"{count} 个{category}" for category, count in counts.items())

def _bounded_history_warning(*, history_warning: Optional[str], history_reasons: Optional[list[str]]) -> Optional[str]:
    warning = _summarize_history_reasons(history_reasons) if history_reasons is not None else history_warning
    if not warning:
        return None
    prefix = "> 历史可能不完整："
    suffix = "…完整明细见 delivery 诊断制品"
    line = prefix + warning
    if len(line) <= MAX_HISTORY_WARNING_CHARS:
        return line
    available = MAX_HISTORY_WARNING_CHARS - len(prefix) - len(suffix)
    return prefix + warning[:available].rstrip("；") + suffix

def render_status_panel(
    rows: list[dict[str, Any]], *, history_warning: Optional[str] = None,
    history_reasons: Optional[list[str]] = None,
) -> str:
    """Render the public sticky panel from rows only.

    The renderer is deliberately a pure projection: it does not read a prior
    comment, infer history from rendered Markdown, or mutate its input. Rows
    are sorted by their durable run identity so a rerun produces the same
    body for the same input set.
    """
    ordered = sorted(rows, key=lambda row: (row["run_id"], row["run_attempt"]))
    if not ordered:
        raise ValueError("status panel requires at least one terminal row")
    current = ordered[-1]
    current_result = current["gate_result"]
    lines = [
        PANEL_MARKER,
        "",
        "### Required Gate v2 — 状态面板",
        "",
        f"当前状态：**{current_result}** · **{_panel_action(current)}**",
    ]
    if current_result == "skipped":
        lines.extend(["", "> 主审未跑，绿≠过审。draft / fork / hosted 的跳过不代表真实通过。"])
    lines.extend([
        "",
        f"当前裁决：`{current['classification']}` / `{current['reason_code']}`",
    ])
    if current_result == "fail" and "primary_audit" in current:
        lines.extend(_primary_findings_panel_lines(current["primary_audit"]))
    current_shadow = current.get("convergence_shadow")
    if isinstance(current_shadow, dict):
        if current_shadow["history_status"] == "history_unavailable":
            shadow_text = "history_unavailable（未估算累计轮数）"
        else:
            shadow_text = (
                f"{current_shadow['eligible_rounds']}/{current_shadow['limit']} · "
                f"{current_shadow['terminal_decision']}"
            )
        lines.extend(["", f"PR 累计主审轮（影子）：`{shadow_text}`"])
    warning_line = _bounded_history_warning(history_warning=history_warning, history_reasons=history_reasons)
    if warning_line:
        lines.extend(["", warning_line])
    recorded_claims = [
        line for line in (current.get("recorded_disposition_claims") or [])
        if isinstance(line, str) and line
    ]
    if recorded_claims:
        lines.extend(["", "Disposition receipts consumed:"])
        for line in recorded_claims:
            lines.append(f"- {line}")
    resolved = [
        line for line in (current.get("resolved_findings") or [])
        if isinstance(line, str) and line and "resolved by receipt" not in line
    ]
    if resolved:
        lines.extend(["", "Resolved:"])
        for line in resolved:
            lines.append(f"- {line}")
    relation = current.get("finding_relation")
    conflict_items = []
    if isinstance(relation, dict):
        conflict_items = [
            item for item in (relation.get("items") or [])
            if isinstance(item, dict) and item.get("relation_to_previous") == "conflict"
        ]
    if conflict_items:
        lines.extend(["", "#### 跨轮冲突（人工裁决 manual_required）", ""])
        for item in conflict_items:
            lines.append(
                f"- `{item.get('id')}` 与上一轮 `{item.get('previous_finding_id')}` "
                f"在 `{item.get('file')}:{item.get('line')}` 冲突"
            )
    carried = (relation.get("carried_findings") or []) if isinstance(relation, dict) else []
    if current_result == "fail" and carried:
        lines.extend(["", "#### 上一轮未处置的 sol 问题（已并入本轮主审）", ""])
        for item in carried:
            lines.append(f"- `{item.get('id')}` ← `{item.get('carried_from_head_sha')}`（{item.get('evidence_reason')}）")
        lines.append("出口：改代码，或对本轮该条签发 rejected。")
    lines.extend([
        "",
        "#### Gate 历史（v1；来源为持久化 `gate_terminal` 制品）",
        "",
        "| Run | Attempt | Head | 状态 | PR 累计主审轮（影子） | 收件人动作 |",
        "| ---: | ---: | :--- | :--- | :--- | :--- |",
    ])
    for row in ordered:
        short_sha = row["head_sha"][:7]
        run_link = f"[{row['run_id']}](https://github.com/{row['repository']}/actions/runs/{row['run_id']})"
        shadow = row.get("convergence_shadow")
        if not isinstance(shadow, dict):
            shadow_text = "—"
        elif shadow["history_status"] == "history_unavailable":
            shadow_text = "history_unavailable"
        else:
            shadow_text = f"{shadow['eligible_rounds']}/{shadow['limit']} · {shadow['terminal_decision']}"
        lines.append(
            f"| {run_link} | {row['run_attempt']} | `{short_sha}` | "
            f"`{row['gate_result']}` | `{shadow_text}` | {_panel_action(row)} |"
        )
    lines.extend([
        "",
        "历史行按 `run_id` + `run_attempt` 去重并只增不删；删除本评论后可由 `gate_terminal` 制品重建。",
        "",
    ])
    return "\n".join(lines)
