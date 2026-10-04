# C 独立审查 verdict

- failure-visibility: p2-only
- standarddelegateoutcome: completed
- review-verdict: pass
- dispatch-id: `dlg-20261004-140024-492e5a`
- risk-tier: personal
- reviewed-range: `6fd21e0ab5a27a279fe5cd15dd74edaa0b079fe9..0973534ee6ce21ca90c6d08f5acc80d8e0f63e25`
- reviewed-repo: `zlxlabs/gate`，gate PR282
- source changes made by this review: none；本文件是唯一新增仓库文件

## 1. 范围与结论

审查覆盖固定 H0..H1 全部 7 个变更文件，重点核对一次 `resolve_policy.py` 解析、完整 JSON（含
`reviewers` 的 `model`/`auth`）、同一 registry 旁的 40-hex `REGISTRY_COMMIT`，以及
`GITHUB_OUTPUT` → job outputs → matrix leg env → 实际 `review-shadow` argv 的传递链。

当前 C diff 的生产路径符合锁定 spec：没有从 leg 重新选择 policy，没有把 JSON 插入 shell
源代码，没有改 gate-v2/labels/runnerselector，也没有新增 digest/newpool/fallback。未发现
P1（数据丢失、静默错误或崩溃）问题；以下 P2 是测试契约盲区，不阻塞 personal 档合并，但应
保留为 backlog。B gate-hub 新 CLI 的真实跨仓消费不由本 verdict 放行。

## 2. Findings

### F-1（P2）：stale registry 负控没有让 leg 消费被篡改的文件

- spec：matrix leg 必须只消费 resolve job 固定传下来的 snapshot，不得从 leg 镜像重新选 policy。
- file/line：`tests/test_gate_shadow_v2_contract.py:1059-1075`。
- trigger：测试在 `tmp_path/gate-hub/registry.yaml` 写入 stale 值（1065），随后调用
  `_run_extracted_leg(leg_dir, ...)`（1068）；该 helper 会在另一个 `leg_dir` 重新创建 hub
  和 canonical `registry.yaml`（158-165），所以被修改的文件没有进入被测 leg。
- consequence：未来 consumer 回归为读取 leg 本地 registry 时，该测试仍可能通过，错误 policy
  会以 shadow 证据的形式静默漂移。
- P1 两问：当前 C workflow 的 consumer 只收 env，故本 diff 的真实触发路径未成立；即使
  回归触发，personal shadow 只影响校准证据、不直接决定 gate，按 P2 处理。
- 实测：把 new consumer 变异为读取自身 leg 的 `registry.yaml` 后，该测试仍为
  `1 passed, 90 deselected`；说明测试没有覆盖声明的 stale 文件。
- 处置：P2 backlog；让同一个 leg helper 实际复用被篡改 registry，或保留真实 producer→leg
  fixture 后再做 mutation red。

### F-2（P2）：旧 CLI 真程序只在可选环境检查，仓库测试主要依赖 synthetic fixture

- spec：旧 `review-shadow` 面对新的 `--require-resolved-policy` 前置参数必须真实非零，
  不能用 env 假绿。
- file/line：`tests/test_gate_shadow_v2_contract.py:1078-1093`。
- trigger：测试必跑的是仓内 `old-review-shadow` synthetic fixture；真实 CLI 只在
  `GATE_HUB_DIR` 或 `/opt/gate-hub` 存在时才执行（1084-1093）。本次验证环境两者均不存在，
  因此该分支实际被跳过。
- consequence：CI 可在没有固定旧 CLI 的环境中长期绿，无法锁住 B 发布前 C 调用旧消费者必红
  的兼容边界。
- P1 两问：旧 CLI 真实调用确会触发，但后果是 leg 明确失败而非静默放行；personal 档且
  发布顺序已明确要求先部署 B，因此不是 P1，属于 P2 测试缺口。
- 实测：从 gate-hub 固定 `416b10aea6b7f9abdf29409b2ca88a3764be9b0a` git archive
  组装实际 CLI 及其依赖后执行新 argv，返回 `rc=3` 和 `usage: review-shadow`；但当前仓库
  测试没有把这个固定程序纳入必跑路径。
- 处置：P2 backlog；在不读 B 未冻结工作树的前提下，用固定 git archive 真实旧 CLI 作为
  producer/consumer 负控 fixture。

### F-3（P2）：resolve producer argv 的 stub 没有约束 repository 参数

- spec：producer 必须实际调用一次 `resolve_policy.py <registry-file> <owner/repo>`，
  不得从错误 source 或 leg 镜像重新选择。
- file/line：`tests/test_gate_shadow_v2_contract.py:40-44, 154-155`；静态检查
  `976-995`。
- trigger：`_STUB_RESOLVE_POLICY` 只读取 `sys.argv[1]` 并输出 fixture，忽略
  `sys.argv[2]`；因此 workflow 若删除 `"${{ github.repository }}"`，该 producer 测试仍能通过。
- consequence：测试不能证明真实 producer argv 的 repo source，调用目标错误时只能依赖运行时
  resolver 自身报错，契约测试不会先行阻止回归。
- P1 两问：当前 workflow 第 598 行的实际 argv 正确，变异只暴露测试盲区；错误 argv 会让
  真实 resolver fail-loud，不会静默采用另一 policy，因此按 P2 处理。
- 实测：删除 workflow 中 repository 参数的 mutation 后，
  `test_resolve_shell_producer_writes_full_json_github_output_bytes` 仍为
  `1 passed, 90 deselected`。
- 处置：P2 backlog；stub 应断言精确 argv（registry path、repo slug、无额外 source 参数），
  或直接使用固定 resolver archive 做 producer fixture。

### P3：测试文件 EOF 多一个空行

`git diff --check H0 H1` 仅报告 `tests/test_gate_shadow_v2_contract.py:1094: new blank line at EOF`；
不影响运行语义，不阻塞本次审查。

## 3. 验证证据

| 项目 | 结果 | 证据 |
|---|---|---|
| 固定范围受影响测试 | pass | `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_shadow_v2_contract.py`：`91 passed` |
| 真实 fixed-base resolver producer | pass | 从 gate-hub `416b10a...` archive 取真实 `resolve_policy.py` 与 registry，执行抽出的 resolve step；`producer_rc=0`，实际 `GITHUB_OUTPUT` 为 2262 bytes，含四个预期输出字段 |
| producer 输出字节/完整 JSON | pass | fixture 断言保留完整 reviewers、带引号与换行的 model、auth、40-hex registry commit；`GITHUB_OUTPUT` heredoc round-trip JSON 成功 |
| 实际 stub consumer env/argv | pass | 91 测试中的 producer→leg probe 断言 `argv=["--require-resolved-policy","42","pi-glm-quote"]`、完整 policy 与 registry commit，consumer capture 单次落盘 |
| 缺 JSON/source 与非法 SHA 负控 | pass | 新 consumer 在缺失、空值、非法 JSON、非法 SHA 时非零且不落 capture；旧参数未知 flag 也非零 |
| 固定旧 CLI 新前置参数负控 | pass | 固定 gate-hub archive 实际执行返回 `rc=3`，输出 `usage: review-shadow` |
| actionlint | pass | 固定 H1 的 `.github/workflows/gate-shadow-v2.yml` 无输出错误 |
| pincheck | pass | `OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative` |
| git diff whitespace | fail/P3 | 仅 EOF 空行，见 F3 后的 P3 观察 |
| OCR 前置扫描 | skipped | 唯一调用 `ocr-review`；primary 仅有 `event=start`，635146 ms 无完整 JSON envelope，已终止卡住包装器；未调用裸 OCR、未做模型实调 |

## 4. 未核项与发布条件

- `failure-visibility` 是 `p2-only`；没有发现 P1。P2/P3 不构成 personal 档合并阻塞。
- B gate-hub 新 CLI 的真实实现、真实消费环境、跨仓 producer→consumer 集成未在本卡验证；
  C mock/stub 通过不推出 B 通过。必须先完成 B 运行时准备，再考虑抬 `v2`。
- 旧 CLI 在 B 未部署前会对新前置参数 fail-loud；这是已确认的发布顺序风险，不应通过忽略退出码
  或 fallback 掩盖。
- 主干基线的 GitHub API 作业数据在派发时不可用，继承红/新红无法据此比较；本 verdict 只报告
  本地固定 diff 的离线证据。
