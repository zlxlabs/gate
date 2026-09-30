<!-- delegate-outcome: succeeded -->

## 目标与结论

实测 reusable workflow 内表达式命名的 job，在卡面同构结构下 draft 检查显示 `gate / gate (draft)`，ready 检查显示 `gate / gate`；整次重跑沿用 draft 名，`format()` 写法结果一致。没有修改生产 workflow。

- 执行器：`codex`；模型：`gpt-6-luna`；Task-Id：`gate-20260930-01`；dispatch：`dlg-20260930-005114-bc973e`。
- 卡分支：`card/gate-260930-a0`，基线 `85916ed5c4f9d181fec866f15d1bb5f58111c9c8`。探针 PR：#272；初始探针提交 `afd42450d9719e7fc5a09fb3f9c02437e3a24428`，format 变体提交 `f6f84257500089c37e5514859c57d2a2d016f3a4`。
- caller workflow `name: gate`、job id `gate`，通过 `uses: ./.github/workflows/probe-name-called.yml` 调用；called workflow 只有 job id `gate`，有 `if: always()` 和 `runs-on: ubuntu-latest`，未使用 secrets。

## 五项观察（Checks API 原始字段）

1. Draft PR opened，run `36652664400` / attempt 1 / suite `99262213756`：名称为 `gate / gate (draft)`，不是字面表达式。

```json
{"check_suite":99262213756,"conclusion":"success","name":"gate / gate (draft)","status":"completed"}
```

2. 执行 `gh pr ready 272` 后，run `36652896393` / suite `99262835133`：名称变为 `gate / gate`，与问题 1 不同。

```json
{"check_suite":99262835133,"conclusion":"success","name":"gate / gate","status":"completed"}
```

3. 对 draft 原始 run `36652664400` 执行 `gh run rerun 36652664400` 整次重跑后，run attempt 2 仍关联 suite `99262213756`，名称仍为 `gate / gate (draft)`；重跑没有产生新的 `gate / gate`。同一 SHA 上的 ready 检查来自另一个 suite `99262835133`：

```json
{"check_suite":99262213756,"conclusion":"success","name":"gate / gate (draft)","status":"completed"}
{"check_suite":99262835133,"conclusion":"success","name":"gate / gate","status":"completed"}
```

4. 执行 `gh pr ready 272 --undo` 转回 draft 后，run `36653038483` / suite `99263209903` 的名称为 `gate / gate (draft)`。

```json
{"check_suite":99263209903,"conclusion":"success","name":"gate / gate (draft)","status":"completed"}
```

5. 将 called job 改成 `name: ${{ format('gate{0}', github.event.pull_request.draft && ' (draft)' || '') }}` 并在 draft PR 推送后，synchronize run `36653109164` / suite `99263399420` 的名称仍为 `gate / gate (draft)`，与问题 1 一致。

```json
{"check_suite":99263399420,"conclusion":"success","name":"gate / gate (draft)","status":"completed"}
```

## 清理、验证与接手核查

- 探针 PR #272 已关闭（GitHub API `state: CLOSED`）；`git ls-remote --heads origin probe/gate-270-check-name` 退出 0、stdout 为空。临时 worktree 和本地探针分支也已删除。
- 本仓标准 CI run `36653108955` success；API 字段：`{"check_suite":99263398869,"conclusion":"success","name":"actionlint","status":"completed"}`、`{"check_suite":99263398869,"conclusion":"success","name":"test","status":"completed"}`。test job 用时 1m55s。
- `actionlint .github/workflows/probe-name-caller.yml .github/workflows/probe-name-called.yml` 与 `git diff --check` 均退出 0。卡面验证 `python3 scripts/check_pinned_uses.py` 退出 0，输出：`OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative`。
- 报告写入、尚未暂存时的实际命令回显：`git log --oneline -1` → `85916ed Merge pull request #261 from zlxlabs/feat/pubexp-ledger-rm`；`git status --short --untracked-files=all` → `?? docs/sessions/260930-gate-270-271/probe-check-name.md`。
- pickup brief：无既有交接单；收件箱有 #270 与相邻 #271。欠账巡检：`summary: orphan 0 owned 0 unattributable 0 too-new 0 recent-7d 0 stale-over-7d 0 missing_ledger_repos 0`（巡检项未展开，需要时跑 `/worksite-audit`）。无本仓 memory 索引；memory 巡检因 `memory_dir_mismatch` 失败，具体本机路径和恢复命令保留在私有派发报告。

## 收尾必答

- 踩到的坑：首次提交被 verification-root pre-commit 闸拒绝；按卡面许可对两个精确探针路径作单次豁免后提交，未改钩子。首次 push 被 public-scan 拒绝本机路径；脱敏后发现旧版提交也随 push 发布，于是核实远端只有本卡两个提交，并以 lease 将本分支改写为仅含脱敏报告的提交。一次 API 复查因手抄 SHA 顺序错误返回 422，改从 PR `headRefOid` 取值后查询成功。handoff 脚本说明要求用 Python 调 `.sh` 曾报语法错误，改用 Bash 成功。
- 闸与绕过：命中 verification-root 提交闸；按卡面探针分支例外，仅对两个精确文件设置单次 `DELEGATE_VERIFICATION_ROOT_OK`，没有禁用 hook 或扩大豁免路径。public-scan 阻止发布本机路径；脱敏并对本分支做带 lease 的历史清理后通过，未绕过扫描。
- 与卡面的偏差：无；探针文件只存在于一次性分支，卡分支只提交本报告。
- 最贵的一步：等待 PR 标准 CI 的 test job（1m55s）。

动态 job 名方案：**有条件可行**——在本报告所测同构 caller/reusable-workflow 结构下，draft 与 ready 检查名分离，原 draft run 重跑仍保持 draft 名；`format()` 变体一致。
