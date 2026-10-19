"""E 段守卫：gate-v2.yml 的每个 Silo 触点步骤必须显式声明所属分区。

背景（design A/E 段、gate#348/#312）：gate#339 新增的归档步骤忘了 continue-on-error，
把「记账」故障放大成了门禁失败；且有测试反过来锁死「记账失败必须变红」。本守卫把分区
上升为机器强制的契约：

- 判定区（verdict）：判定必需，fail-closed。禁止 continue-on-error；禁止输出
  GATE-ARCHIVE-DEFERRED（判定失败必须真实变红，不允许降级为告警）。
- 记账区（bookkeeping）：留存/诊断/归档。必须 continue-on-error: true（GitHub 语义：
  step outcome=failure 但 job conclusion 不变）；上传/归档类（put/put-dir/put-review-record）
  的 run 块必须在失败路径输出固定格式 `GATE-ARCHIVE-DEFERRED kind=<kind> key=<silo key>
  reason=<错误类别>`（::warning:: 同时输出）——该行是 G3 补传队列的输入契约。

声明形式（执行器选定）：步骤块内恰一行注释 `# gate-silo-region: verdict` 或
`# gate-silo-region: bookkeeping`。选注释而非步骤名前缀的理由：步骤名是大量既有契约
测试的定位锚点，改前缀会放大无关改动；注释零运行时影响，且本守卫同时做原文切块校验，
避免「YAML 解析后注释丢失导致漏检」。

触点判定（invoke 而非 mere mention）：步骤 run 块中出现
- `"$SILO_EXEC"`（silo_exec.sh 的调用），或
- `gate_bounded_retry.py" magicdns --silo-store`（Silo DNS 准备，失败即全部记账失败）。
仅把 `scripts/silo_store.py` 列进 sparse checkout 的步骤不是触点（只取文件，不碰 Silo）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-v2.yml"

REGION_VERDICT = "verdict"
REGION_BOOKKEEPING = "bookkeeping"
REGION_COMMENT = re.compile(r"#\s*gate-silo-region:\s*(verdict|bookkeeping)\b")
STEP_HEADER = re.compile(r"^      - (?:name: (.*)|uses: (.+))$", re.MULTILINE)
UPLOAD_VERB_RE = re.compile(r'\$SILO_EXEC"?\s+(put|put-dir|put-review-record)\b')
DEFERRED_LINE = "GATE-ARCHIVE-DEFERRED"


def _silo_touchpoint(block: str) -> bool:
    if "$SILO_EXEC" in block or 'gate_bounded_retry.py" magicdns --silo-store' in block:
        return True
    # The verdict channel itself (design A): the primary-side producer writing
    # the audit output and the gate/ledger-side consumers reading it are part
    # of the verdict/bookkeeping partition even though they never touch Silo.
    if 'audit_json_b64=' in block or 'needs.primary.outputs.audit_json_b64' in block:
        return True
    return False


def _declared_region(block: str) -> str | None:
    matches = REGION_COMMENT.findall(block)
    if not matches:
        return None
    if len(matches) > 1:
        return f"conflicting:{matches}"
    return matches[0]


def _split_step_blocks(text: str) -> list[str]:
    """Split raw workflow text into per-step blocks (6-space list items).

    A block includes the comment lines immediately above its `- name:`/`- uses:`
    header — the region declaration lives there. YAML parsing drops comments,
    so the declaration is validated on raw text; the caller separately
    validates the same steps via the parsed tree.
    """
    headers = list(STEP_HEADER.finditer(text))
    if not headers:
        return []
    lines = text.splitlines(keepends=True)
    spans = []
    for match in headers:
        start_line = text.count("\n", 0, match.start())
        begin = start_line
        while begin > 0 and re.fullmatch(r"      #.*\n", lines[begin - 1]):
            begin -= 1
        spans.append((begin, start_line))
    blocks = []
    for i, (begin, _header) in enumerate(spans):
        end_line = spans[i + 1][0] if i + 1 < len(spans) else len(lines)
        blocks.append("".join(lines[begin:end_line]))
    return blocks


def find_silo_region_violations(workflow_text: str) -> list[str]:
    """Return every human-readable violation; empty list means the guard holds.

    Importable so tests can feed mutated inputs directly (red-proof harness).
    """
    parsed = yaml.safe_load(workflow_text)
    if not isinstance(parsed, dict) or "jobs" not in parsed:
        return ["workflow is not a jobs mapping; cannot run silo-region guard"]
    violations: list[str] = []
    blocks = _split_step_blocks(workflow_text)
    # Map each raw block to its parsed step by name (blocks may also be job-level
    # keys like `outputs:` if a future edit indents wrong — name-less blocks that
    # carry a touchpoint must fail loudly, not be silently skipped).
    named_steps: dict[str, dict] = {}
    nameless_steps: list[dict] = []
    for job_name, job in parsed["jobs"].items():
        for step in job.get("steps", []) or []:
            if step.get("name"):
                named_steps[(job_name, step["name"])] = step
            else:
                nameless_steps.append(step)

    for block in blocks:
        if not _silo_touchpoint(block):
            header = STEP_HEADER.search(block)
            label = (header.group(1) or header.group(2) or "?").strip() if header else "?"
            # touchpoint-free block: only guard against misuse of the marker
            if REGION_COMMENT.search(block):
                uses_silo_scripts = "silo_store.py" in block or '"$SILO_STORE"' in block
                if not uses_silo_scripts:
                    violations.append(
                        f"step {label!r} declares gate-silo-region but does not invoke Silo"
                    )
            continue
        header = STEP_HEADER.search(block)
        label = (header.group(1) or header.group(2) or "?").strip() if header else "?"
        region = _declared_region(block)
        if region is None:
            violations.append(
                f"step {label!r} invokes Silo but declares no gate-silo-region "
                "(expected one comment: '# gate-silo-region: verdict' or '# gate-silo-region: bookkeeping')"
            )
            continue
        if region.startswith("conflicting:"):
            violations.append(f"step {label!r} declares multiple gate-silo-region comments: {region}")
            continue
        # locate the parsed step for structural assertions (any job; names are
        # unique per job and this workflow keeps step names globally unique)
        step = None
        owner_job = None
        for (job_name, name), candidate in named_steps.items():
            if name == label:
                step = candidate
                owner_job = job_name
                break
        if step is None:
            # `uses:`-only steps have no name; match by run text
            for candidate in nameless_steps:
                if str(candidate.get("run", "")) and str(candidate.get("run", "")) in block:
                    step = candidate
                    owner_job = "<nameless>"
                    break
        if step is None:
            violations.append(
                f"step {label!r} declares gate-silo-region={region} but was not found in the parsed workflow"
            )
            continue
        continue_on_error = step.get("continue-on-error")
        run = str(step.get("run", ""))
        if region == REGION_VERDICT:
            if continue_on_error is not None:
                violations.append(
                    f"step {label!r} is verdict-region but sets continue-on-error={continue_on_error!r} "
                    "(verdict steps must fail closed)"
                )
            if DEFERRED_LINE in run:
                violations.append(
                    f"step {label!r} is verdict-region but emits {DEFERRED_LINE} "
                    "(verdict failures must fail the job, not degrade to a warning)"
                )
        else:
            # "Never changes the job conclusion": either a step-level
            # continue-on-error, or the owning job has a job-level one (the
            # ledger job does — a failed step there skips the row build but the
            # conclusion stays green).
            owner = parsed["jobs"].get(owner_job or "", {})
            if continue_on_error is not True and owner.get("continue-on-error") is not True:
                violations.append(
                    f"step {label!r} is bookkeeping-region but neither it nor its job sets "
                    "continue-on-error: true (a bookkeeping failure must never change the job conclusion)"
                )
            is_upload = bool(UPLOAD_VERB_RE.search(run))
            if is_upload:
                if DEFERRED_LINE not in run:
                    violations.append(
                        f"step {label!r} is a bookkeeping upload but never emits {DEFERRED_LINE} "
                        "(G3 的补传队列以该行为输入契约)"
                    )
                for token in ("::warning::", "kind=", "key=", "reason="):
                    if token not in run:
                        violations.append(
                            f"step {label!r} emits {DEFERRED_LINE} but its run block lacks {token!r}"
                        )
        if region == REGION_BOOKKEEPING and owner_job is None:
            violations.append(f"bookkeeping step {label!r} not found in any job")
    return violations


def test_every_silo_touchpoint_declares_a_region():
    violations = find_silo_region_violations(WORKFLOW.read_text(encoding="utf-8"))
    assert violations == [], "\n".join(violations)


def test_guard_counts_every_expected_touchpoint():
    """恒真断言自检：守卫必须扫到已知触点集合，而不是扫了个空集。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    blocks = [b for b in _split_step_blocks(text) if _silo_touchpoint(b)]
    labels = []
    for block in blocks:
        header = STEP_HEADER.search(block)
        labels.append((header.group(1) or header.group(2) or "?").strip())
    # 判定区消失（gate resolve/download 删除后）——触点全集即记账区 + 判定通道准备。
    for expected in (
        "Publish canonical primary audit to job output",
        "Upload canonical primary audit",
        "Upload primary review diagnostics",
        "Archive primary review diagnostics permanently",
        "Archive raw primary review evidence permanently",
        "Upload advisory review event",
        "Archive advisory review event permanently",
        "Upload convergence receipt",
        "Archive convergence receipt permanently",
        "Upload gate terminal envelope",
        "Retry upload gate terminal envelope",
        "Upload gate status panel delivery diagnostic",
        "Archive gate status panel delivery diagnostic permanently",
        "Resolve v2 ledger artifacts",
        "Read canonical primary audit from primary job output for ledger",
        "Upload v2 review effectiveness ledger",
        "Upload v2 review effectiveness ledger latest pointer",
        "Upload v2 raw review ledger to permanent archive",
        "Archive canonical primary audit permanently",
        "Archive gate terminal envelope permanently",
        "Upload live review record/v1 to permanent Silo",
    ):
        assert expected in labels, f"guard no longer sees touchpoint {expected!r}; labels={labels}"
    # 每个 job 的 MagicDNS 准备步骤也在触点集合内
    assert sum(1 for label in labels if label == "Resolve Silo hostname via MagicDNS") == 4


def _minimal_workflow_from_real_text() -> str:
    """Real workflow's publish + audit-upload steps as a minimal jobs mapping."""
    text = WORKFLOW.read_text(encoding="utf-8")
    blocks = _split_step_blocks(text)
    publish = next(b for b in blocks if "- name: Publish canonical primary audit to job output" in b)
    upload = next(b for b in blocks if "- name: Upload canonical primary audit" in b)
    return "jobs:\n  primary:\n    steps:\n" + publish + upload


def test_guard_rejects_new_undeclared_silo_step_red_proof():
    """反例红验：新增一个未声明分区的 Silo 步骤必须让守卫红。"""
    golden = _minimal_workflow_from_real_text()
    assert find_silo_region_violations(golden) == [], "golden input must be clean before mutation"
    mutated = golden + (
        "      - name: Future archive someone invents\n"
        "        if: always()\n"
        "        run: |\n"
        '          set -euo pipefail\n'
        '          "$SILO_EXEC" put --tier archive --repo-id "${{ github.repository_id }}" \\\n'
        '            --name "future-archive" --file "${{ runner.temp }}/x.json"\n'
    )
    violations = find_silo_region_violations(mutated)
    assert violations, "undeclared new Silo step must turn the guard red"
    assert any("Future archive someone invents" in v for v in violations)
    # 同一步骤声明为 bookkeeping 后，缺 continue-on-error 仍红
    mutated2 = mutated.replace(
        "      - name: Future archive someone invents\n",
        "      # gate-silo-region: bookkeeping\n"
        "      - name: Future archive someone invents\n",
    )
    violations2 = find_silo_region_violations(mutated2)
    assert violations2, "bookkeeping without continue-on-error must stay red"
    # 补齐 continue-on-error + deferred 行后绿
    mutated3 = mutated2.replace(
        "      - name: Future archive someone invents\n        if: always()\n",
        "      - name: Future archive someone invents\n        if: always()\n        continue-on-error: true\n",
    ).replace(
        '          "$SILO_EXEC" put --tier archive --repo-id "${{ github.repository_id }}" \\\n'
        '            --name "future-archive" --file "${{ runner.temp }}/x.json"\n',
        '          if ! "$SILO_EXEC" put --tier archive --repo-id "${{ github.repository_id }}" \\\n'
        '            --name "future-archive" --file "${{ runner.temp }}/x.json"; then\n'
        '            echo "::warning::GATE-ARCHIVE-DEFERRED kind=future key=archive/future reason=unclassified"\n'
        "          fi\n",
    )
    assert find_silo_region_violations(mutated3) == [], "fully-declared bookkeeping step must pass"


@pytest.mark.parametrize(
    ("mutation", "expect_substring"),
    [
        ("verdict-with-continue-on-error", "continue-on-error"),
        ("verdict-with-deferred", "GATE-ARCHIVE-DEFERRED"),
        ("bookkeeping-upload-without-deferred", "GATE-ARCHIVE-DEFERRED"),
    ],
)
def test_guard_rejects_region_misuse(mutation, expect_substring):
    golden = _minimal_workflow_from_real_text()
    assert find_silo_region_violations(golden) == [], "golden input must be clean before mutation"
    if mutation == "verdict-with-continue-on-error":
        # re-label the real upload step as verdict: its continue-on-error now
        # violates the fail-closed verdict contract
        mutated = golden.replace(
            "      # gate-silo-region: bookkeeping\n      - name: Upload canonical primary audit",
            "      # gate-silo-region: verdict\n      - name: Upload canonical primary audit",
            1,
        )
    elif mutation == "verdict-with-deferred":
        mutated = golden.replace(
            "      # gate-silo-region: bookkeeping\n      - name: Upload canonical primary audit",
            "      # gate-silo-region: verdict\n      - name: Upload canonical primary audit",
            1,
        )
        assert "GATE-ARCHIVE-DEFERRED" in mutated
    else:
        # strip the deferred emission lines from the bookkeeping upload
        lines = golden.splitlines(keepends=True)
        kept = [
            line
            for line in lines
            if not (line.lstrip().startswith('echo "::warning::GATE-ARCHIVE-DEFERRED') and "kind=primary-audit " in line)
        ]
        assert len(kept) < len(lines), "mutation must actually remove a deferred line"
        mutated = "".join(kept)
    violations = find_silo_region_violations(mutated)
    assert violations, f"{mutation} must turn the guard red"
    assert any(expect_substring in v for v in violations)
