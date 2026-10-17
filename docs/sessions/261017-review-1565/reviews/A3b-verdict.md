# A3b live compact record 独立审查 verdict

failure-visibility: p1-found

## 判定

**FAIL，阻断。** 在 PR #337 可核验的 head b50088c14d2e3094dd0ae8ef5e22c4fd4f263df3 上发现 1 条 P1 和 1 条 P2，均违反卡面不变式。

卡面给出的 head 末位是 df5，该对象不在远端（直接 fetch 返回 not our ref）；refs/pull/337/head 与 gh pr view 均确认实际 PR head 为 df3。因此本 verdict 的实际审查范围是 f8978b55d7823aef078635b09587f16f339d55e2..b50088c14d2e3094dd0ae8ef5e22c4fd4f263df3，没有把两者说成同一个 SHA。

## Findings

### P1 — 未分类的 primary skip 被写成已知 null

- **违反不变式**：#1 未知事实需同时保留 null 和 unknown_fields；#2 只有四种已验证 skip 可被归类。
- **触发与证据**：真实 aggregator 在 primary_result=skipped 且没有合法豁免原因时会输出合法 gate_terminal/v1，其中 reason_code=unexpected_primary_skip、skip_reason=null（.github/actions/gate-aggregator/aggregate.py:1022；tests/test_gate_aggregator.py:984）。在该 envelope 上运行真实 build_ledger.py CLI，sidecar 写出 skip.reason=null，但 provenance.unknown_fields 没有 skip.reason；实际 gate-hub v1 消费端仍接受该记录。
- **后果**：sidecar 把“primary 确实 skipped、但不属于四种合法 skip”的状态压成与正常未 skip 相同的已知 null。后续读取 skip.reason 和 unknown_fields 的消费者无法发现该异常，统计可静默失真。
- **严重度 / 阻断**：P1 / 是，属于可由真实 producer 触发的静默事实错误。

### P2 — 身份不匹配时 CLI 不产出 unknown sidecar

- **违反不变式**：#2 要求 repo id、repo、PR、head、run、attempt 任一不匹配时保持未知。
- **触发与证据**：给真实 CLI 一个 schema/kind 合法但 head_sha 不匹配的 terminal，main() 先调用 build_entry()；旧 ledger 的 terminal 检查在 sidecar 投影前抛 gate terminal identity mismatch。实测退出码为 1，sidecar 文件不存在；新增测试 test_live_record_cli_does_not_project_terminal_skip_across_identity 也把 repo/PR/run/head 不匹配断言为失败退出。
- **后果**：没有错误套用邻接 terminal，但也没有卡面要求的 null + unknown_fields 记录，--record-output 在该输入下无法完成契约。
- **严重度 / 阻断**：P2 / 是，阻断本卡明确要求的 sidecar 产出行为。

## 核验

- 固定 PR head 的窄测：scratch-worktree.sh ... df3 -- uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_review_ledger.py → **232 passed**。
- 在该 head 的 scratch worktree 用真实 build_ledger.py CLI 写 sidecar，再由 gate-hub 主仓 scripts/review/ledger_schema.py::validate_review_record_v1 校验：通过；result.findings=null 保持旧行并输出空 findings；sidecar 为单行 LF 结尾 JSON。
- 同一 v1 消费端接受新增 fixture，也接受从实际 A0 raw ledger fixture 投影的 sidecar。其记录的 source key 与 fixture README 中记录的实际 d30 对象一致：silo/ci-artifacts/d30/1293158679/codex-review-ledger-v2-1293158679-e371990f82a830e2988c69a4858c26bf62bfe651-35858989311-1/ledger.jsonl；raw fixture SHA-256 为 cabc8b5e5237749c47d77cbba1823e94742ca9f47a4e5f50a581d350e7522e22。
- git diff --check f897...e2 df3 → 通过。工作树未改动其他代码。
- 当前工作流/Silo 接线不在本卡范围，未作为 finding；没有运行 OCR 或真实模型调用，也没有 S3 写入。进程环境没有 Silo 凭据，因此未对线上 S3 做实时 GET；source key 依据真实 producer 的 d30 artifact 命名、workflow 写入路径与保留的真实 raw fixture 核验。
- 主干 baseline 卡面注明 gh api request failed：继承红和新增 CI 红均未能判定；窄测结果不替代远端 CI 结论。
