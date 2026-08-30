# gate-B disposition receipt v2：第 2 轮对抗审查 verdict（换家复验）

审查对象冻结为 `dab9c0cbb75ce7aab323397ba54e2e6b8eff63bf..9859e0545cbb6fea209090d13216477a6c5add9c`。H0=`d90f4c9da92efd51929667599833a2123c42df0f`，H1=`9859e0545cbb6fea209090d13216477a6c5add9c`。本轮新证据 = H0..H1 六笔修复提交的 diff + 红验抽查原文 + OCR 对本冻结范围的新扫描 + 对 `_payload_without_auth` / no-op stdout / 面板注入面的降层探针。不把审查期间的新提交纳入本轮。

risk-tier: personal。P1 红线 = 数据丢失 / 静默出错 / 崩溃 / agent 给自己开绿灯。

## H0..H1 增量四问

审查范围 `d90f4c9..9859e05`（524e4bd, e095441, fff7d71, 5398063, 40a290b, 9859e05）。

1. **是否只修登记在案的 R1 P2×4？** 是。P2-1 → `fff7d71` 把 `outcome.resolved_findings` 写入 terminal envelope / panel row，`render_status_panel` 读当前行渲染 `Resolved:` 列表。P2-2 → `524e4bd` + `e095441`：同名 receipt 且去掉授权三字段后 canonical JSON 一致则 no-op 保留原件。P2-3 → `5398063` + `e095441` 补齐 producer/consumer 双侧缺失、空、非字符串、`T` 但非 ISO 负例。P2-4 → `40a290b` 把 `_single_line_reason` 内联进唯一 G4 builder。`9859e05` 只记 closeout。进度文档同步这四条，无第五个功能。
2. **新增抽象勾稽。** `_AUTH_FIELDS` + `_payload_without_auth` 是 P2-2 / 修订幂等条款的钉死点，运行时只有 `_write_immutable` 一个调用方。单消费者仍必要：把「忽略集恰好授权三字段」收口在一处，避免比较分支把 reason/kind/finding_id 也抹掉。测试 `test_disposition_producer_same_params_new_approved_at_is_noop` / `test_disposition_producer_reason_change_still_conflicts` 锁这条边界。不另计熵增 finding。
3. **fallback 检查。** no-op 比较先做整包逐字节相等，再只从 `_AUTH_FIELDS` 三键剥离后比 canonical JSON；reason 变更仍 conflict。畸形既有文件（非 UTF-8 / 非 JSON / 非对象）进 `except (ValueError, json.JSONDecodeError, UnicodeDecodeError)` → `same_body=False` → `immutable artifact conflict`，不是把任意字段当授权戳忽略，也不是静默当成 no-op。
4. **双路径。** G4 句子只在 `required_disposition_lines` 拼一次（契约测试 `test_required_disposition_lines_is_the_only_g4_line_builder` 在 `.github` 下只命中 `convergence.py`）。Step Summary / PR 面板从 `outcome.resolved_findings`（面板再经 terminal envelope/`_panel_current_row` 拷贝）加 `Resolved:` 标题和 `- ` 前缀；`::notice` 打同一行原文、不加 markdown 前缀。这是介质包装，不是第二份语义拼装。

增量审通过，不计入新增 P1。

## Findings

### P2-1（本轮新增）：畸形既有文件 → conflict 的 fail-closed 分支没有改坏即红测试

- 违反条款：spec 7（每处新校验有改坏即红测试）；修订幂等条款「其余差异 → conflict」。H0..H1 为 P2-2 新增 `_payload_without_auth` 解析既有文件，但测试只锁了「仅 `approved_at` 不同 → no-op」和「reason 变更 → conflict」，没有把非 JSON / 非对象 / 非 UTF-8 既有文件打成 `immutable artifact conflict`。
- 证据：`.github/actions/gate-disposition/issue_receipt.py:189-212` 的 `except` 把三类解码失败收成 `same_body=False` 再 conflict；`tests/test_gate_convergence_artifact.py` 无 `_payload_without_auth` 符号、无非 JSON fixture。`test_parse_v1_payload_without_auth_fields_is_schema_version_mismatch` 只是名字碰巧含子串。
- P1 两问：①真实使用会触发吗？生产上 `OUTPUT_DIR` 是 runner.temp，既有文件几乎总是本 job 刚写出的合法 JSON，畸形既有不是常规路径。②后果能否接受？当前实现方向是 conflict（fail-loud），不会把垃圾当成 no-op 放行；缺测试的风险是以后把 `except` 改成 `same_body=True` 时套件仍绿。按 personal 档不升 P1。**接受不修**，记 backlog：补一条既有文件写入 `b"not-json"` / `b"[]"` / 非法 UTF-8 的 subprocess 负例即可锁死漏斗，不要按形态穷举。

已登记不重复计：R1 P2×4（已修）、轴 A/B 归因偏差（P2 观察）、OCR 既有低价值意见。

## 三个语义轴裁决

| 轴 | 真实路径与证据 | P1 两问 | 本仓裁决 |
|---|---|---|---|
| A. `workflow_call` 下 `github.triggering_actor` | `.github/workflows/gate-v2-disposition.yml:155` 注入 `DISPOSITION_APPROVER`。inputs 仍是六项，无 `approver` 输入（`tests/test_gate_v2_contract.py:142-149,177-179`）。授权事实在 `environment: gate-disposition` 的 required reviewers，不进 receipt。 | ①会触发：caller 若由 bot 触发，receipt 把 bot 记成 approver。②后果可接受：不能靠伪造 inputs 绕过 environment 审批，也不会把未审批写成已审批。归因偏差不是静默放绿。 | 不升 P1；维持已登记 P2 观察。spec 明文要求 `triggering_actor`，不另开违反 spec 的 finding。 |
| B. re-run login/id 分叉 | login=`github.triggering_actor`，id=`github.actor_id`（workflow:155-156）。这对只进 receipt / G4 展示；`validate_disposition_receipt` 不拿它做授权判定。同名重签若只改授权三字段，本地 `_write_immutable` no-op 保留**原件**（更接近首次签发人）。跨 run 时 runner.temp 是新的，会写出新 artifact；消费侧第二张同 finding 走 `finding_already_consumed`，不 fail-closed。 | ①会触发。②后果可接受：展示可能不是同一人，但不绕过 environment、不改变 exact-hit。 | 不升 P1；与 A 合并为已登记 P2 观察。 |
| C. v1→v2 扫描边界 | `DISPOSITION_ARTIFACT_PREFIX = "gate-disposition-receipt-v2-"`（`aggregate.py:156`）；`_fetch_disposition_receipts` 下载前按前缀过滤（:1398）。v2 前缀但 parse 失败 `except Exception: continue`（:1412-1416），不入 receipts。无匹配 receipt 时 P1 仍阻塞。`parse_disposition_receipt` 对非 v2 kind 抛 unexpected kind；v1 schema 进 `schema_version_mismatch`。无 v1 兼容分支。 | ①TTL 内可有 v1 残留。②方向是留红不是放绿。 | 安全，无新 finding。 |

## 对抗抽查（本轮增量）

- **畸形既有文件：** 见 P2-1。实现方向正确（conflict），缺漏斗测试。
- **no-op stdout JSON：** `issue()` 仍 `print(json.dumps({"artifact","path","written"}, sort_keys=True))`；no-op 时 `written=False`、JSON 在 stdout、提示在 stderr。workflow `jq -r '.artifact'` 不读 `written`。`test_disposition_producer_same_params_new_approved_at_is_noop` 断言 `returncode==0` 且 `json.loads(second.stdout)["written"] is False`。消费方兼容。
- **面板注入面：** `required_disposition_lines` 先 `reason.split()` 折叠空白再截断 500；面板 `f"- {line}"` 原样写入 markdown。reason 里的 markdown/HTML 可影响评论排版，但 `gate_result` 来自结构化 outcome，不从面板 markdown 回解析；换行已被折叠，无法插入伪历史表行。personal 档注入不是红线，spec 未要求转义。**不单列 finding。**

## 工具标注 / 本仓判定 / 两问答案

| 证据源 | 工具标注 | 本仓判定 | 两问答案摘要 |
|---|---|---|---|
| OCR `ocr-review` dab9c0c..9859e05 | `status=partial`，`coverage=partial`，`cli_status=partial`；11 条 comments（1 high / 若干 low）；复核 1/11 confirmed，其余 unver/超时。**不是 `reviewed` 空 findings，不得写成扫过且干净。** | high（`_legacy_disposition_stub` vs 缺 auth → 不 fail-closed）：不成立。stub 谓词要求 `disposition=="none"` 且全字段空（`convergence.py:480-495`）；`false-positive` 缺 auth 走 `malformed_receipt`（在 `_DISPOSITION_FAIL_CLOSED_REASONS`）。空 stub 是「无 disposition」哨兵，终端是不消费。其余 OCR 条为 workflow 正整数形态、测试字面量、命名/重复 fixture，≤P3 不占循环。 | high：真实 false-positive 收据进不了 stub 桶；空哨兵不消费、不放绿。low：不触发红线。 |
| 红验抽查 | 见下节原文 | 至少一条 AssertionError 转红，不是恒真 | 不适用 |
| `git diff dab9c0c..9859e05 -- .github/workflows/gate-v2.yml templates/` | 无输出 | spec 6：`gate-v2.yml`、caller 模板、40-hex 闸未改；本 diff 只动 `gate-v2-disposition.yml` 的 env 注入 | 不适用 |
| 轴 A/B 设计取舍 | OCR 亦提到 re-run actor | 已登记 P2 观察，不重复开 finding | 见上表 |

## 红验抽查原文

临时 detached worktree：`e0bd30f88e99decb1e35a6601768d41615defbf8`。注入确认：拷入后的 `tests/test_gate_v2_contract.py` 含 `test_required_disposition_lines_is_the_only_g4_line_builder` 与 `test_gate_disposition_receipt_names_include_epoch_and_audit_digest`（63957 bytes）。

**抽查 1（拷入整份新 `tests/test_gate_v2_contract.py`）：**

```
______ test_gate_disposition_receipt_names_include_epoch_and_audit_digest ______
tests/test_gate_v2_contract.py:186: in test_gate_disposition_receipt_names_include_epoch_and_audit_digest
    assert "disposition_receipt_artifact_name" in producer
E   assert 'disposition_receipt_artifact_name' in '#!/usr/bin/env python3\n"""Issue immutable disposition artifacts from a canonical primary audit.\n\naudit_digest is S...pe(args.input_stdin)\n    return issue(args, envelope)\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
FAILED tests/test_gate_v2_contract.py::test_gate_disposition_receipt_names_include_epoch_and_audit_digest
1 failed, 1 passed in 0.19s
```

失败类型是 AssertionError。同文件的 `test_required_disposition_lines_is_the_only_g4_line_builder` 在 base 上绿：它锁的是「`.github` 里 `resolved by receipt` 只出现一次」这条负向不变式，e0bd30f 已成立；再加第二处拼装会红，不是恒真。

**抽查 2：** 整份拷入 `tests/test_gate_convergence.py` 跑 `test_required_disposition_lines_include_approver_and_truncated_reason` 得到 `TypeError: DispositionReceipt.__init__() got an unexpected keyword argument 'approver'`——注入方式撞到旧 dataclass，按纪律作废。改为最小注入（只断言生产源码含本次新增记号）：

```
____________________ test_g4_builder_embeds_approver_token _____________________
tests/test_r2_red_min.py:8: in test_g4_builder_embeds_approver_token
    assert "approved by {receipt.approver}" in text
E   assert 'approved by {receipt.approver}' in '"""Pure canonical clean-streak convergence evaluator.\n\nThis module deliberately has no filesystem, network, subproc...ate = result.state\n        if state.terminal_decision == "fail_closed":\n            return state\n    return state\n'
____________________ test_producer_auth_strip_helper_exists ____________________
tests/test_r2_red_min.py:13: in test_producer_auth_strip_helper_exists
    assert "_payload_without_auth" in text
E   assert '_payload_without_auth' in '#!/usr/bin/env python3\n"""Issue immutable disposition artifacts from a canonical primary audit.\n\naudit_digest is S...pe(args.input_stdin)\n    return issue(args, envelope)\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
2 failed in 0.02s
```

失败类型是 AssertionError。临时 worktree 已 `git worktree remove --force` 回收。

跨发布边界既有锁：`tests/test_gate_convergence_artifact.py:308-355` `test_issue_function_bytes_feed_parse_disposition_receipt`（`issue()` 落盘字节直接 `parse_disposition_receipt`）。本卡不跑被审对象全量套件。

## 熵增审查

- v2 schema / kind / `approver`+`approver_id`+`approved_at`：producer 写入、parser/validator 读取、G4 展示、artifact 名共用，不是单实现接口。
- `_approved_at`（producer）与 `_approved_at_has_time`（consumer）：发布边界两侧独立 fail-closed，有各自负例。
- `_AUTH_FIELDS` / `_payload_without_auth`：见四问②，单消费者有必要理由。
- `DISPOSITION_REASON_DISPLAY_MAX`：G4 截断契约，测试按该常量断言。
- 面板/envelope 的 `resolved_findings` 字段：把已有 list 拷到发布面，不是新状态机。
- `_single_line_reason`：H1 已删，熵 -1。
- `docs/sessions/260830-tri-repo-triage/progress/gate-b-progress.md`：进度记录，运行时不读。
- 未发现为过测试而加的 v1 兼容分支或防御式 fallback。

## 取证命令摘要

- `ocr-review --repo … --from dab9c0cbb75ce7aab323397ba54e2e6b8eff63bf --to 9859e0545cbb6fea209090d13216477a6c5add9c --audience agent --concurrency 4 --background-file /tmp/ocr-bg-gate-b-r2.md`：status=partial / coverage=partial。
- `git diff --stat dab9c0c..9859e05`：9 files, +549/-36。
- `git diff dab9c0c..9859e05 -- .github/workflows/gate-v2.yml templates/caller-gate-disposition.yml templates/caller-gate.yml`：空。

verdict: pass
