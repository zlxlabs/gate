# gate#196 validation

验证记录：2026-09-22

- 已有实现验证：全量 `1128 passed in 103.58s`；相关测试 `522 passed`；定向测试 `4 passed`；pin check 通过。
- 本轮红验：使用 `scratch-worktree.sh` 从 gate base `b353622a3759e163d1c17034008f8c22f2093cf7` 建树，仅覆盖当前 `tests/test_gate_convergence.py`，运行指定重复非法 receipt 用例。
- 红验结果：退出码 1，因 `AssertionError` 失败；旧实现第二条状态为 `(True, True, 'duplicate_receipt_noop')`，预期为 `(False, False, 'unknown_disposition')`，不是 `ImportError`。原始输出见 `validation-red.log`。
- OCR：`/tmp/gate196-ocr-envelope-260922.json` 为 `reviewed` / `minimax` / `coveragecomplete`；一条 low 的重复条件维护建议已接受，未修。
- CI：run `35725472991` 的 `test` 与 `actionlint` 均为 `SUCCESS`。
- 未重跑全量测试或 OCR。
