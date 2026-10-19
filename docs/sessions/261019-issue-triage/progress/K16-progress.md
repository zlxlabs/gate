# K16-progress

## 2026-10-19 · 修前扫描与第三支收窄

- 当前阶段：implementing
- 本段结论：agent-config `origin/main` `2f7e7fb17ee3eb53d479be3c8272e42912961f2e` 上 TOKEN 第三支实测命中 20 行 / 18 文件（不是卡面写的 13 行 / 11 文件）。判据定为「混合大小写，或含 base64 附加符 `+/=`」；kebab / 单大小写标识符不命中。前两支保持原样。
- 关键决策与已否决方案：否决「字母+数字即命中」——`fake-token-setenv-181-not-a-real-secret` 这类带数字的 kebab 仍会误报，修后无法让主干清单清零。仓外证据：`~/.local/state/gate-hub/261019-issue-triage/K16-agent-config-main-third-branch-pre.json` sha256 `8d6ae28b8a6a5c1ce3b13a9e50086a4d142ae651fbcd599d94d2f0bc7a702f49`。
- 下一步唯一动作：跑 `tests/test_scrub_outbound.py`，绿则提交本段。

## 2026-10-19 · 实现与修后对照

- 当前阶段：implementing
- 本段结论：第三支已收窄；`tests/test_scrub_outbound.py` 26 passed。修前 20 行用生产函数 `scrub_outbound_text` 复扫，TOKEN 命中 0。召回样本 ghp_/github_pat_/sk-/AKIA/Bearer/`password: Xy7…`/高熵 base64 仍为 TOKEN。
- 关键决策与已否决方案：`token=ghp_…` 现在由前缀支命中值本身（输出 `token=[REDACTED:TOKEN]`），不再被第三支整段吞掉；不把前缀形态硬塞回第三支。仓外修后清单 sha256 `5888f7aed96c654bedb29ed190c4e5155c2559ce389d269372eda786170f2878`。
- 下一步唯一动作：提交本段后做反向红验（还原第三支，kebab 用例须 AssertionError）。

## 2026-10-19 · 反向红验

- 当前阶段：implementing
- 本段结论：去掉第三支的混合大小写/`+/=` 先行断言后，`# token: acceptance-abandon-failed` 与 `token: acceptance-abandon-failed` 均以 AssertionError 变红（`('# [REDACTED:TOKEN]', ['TOKEN'])` / `('[REDACTED:TOKEN]', ['TOKEN'])`）。已只还原该处 lookaround，生产文件与 HEAD 一致，26 passed。
- 关键决策与已否决方案：无。仓外红验原文 sha256 `6be2aa771c063b5c9722380f324287e24137d276561a5c1583e5270a688d5c22`（`K16-redtest-kebab.txt`）。
- 下一步唯一动作：scope 检查、全量测试、push 并开 PR。

## 2026-10-19 · 续修：scope 绿、全量 2 红、已申请扩 scope

- 当前阶段：implementing
- 本段结论：`card_scope_check.py --range 72c6ec1..HEAD` 3 文件通过；`tests/test_scrub_outbound.py` 26 passed。全量 `2 failed, 1878 passed`：归档路径仍用 `token: synthetic-owner-token-abcdefghijklmnop123456`（全小写 kebab+数字）当 TOKEN 召回。已否决把字母+数字加回第三支。已申请 scope seq=1：`tests/test_gate_v2_raw_evidence_scrub.py`、`tests/test_gate_v2_diagnostics_upload.py`，把 fixture 改成混合大小写凭据形态。
- 关键决策与已否决方案：合入 origin/main 后 envelope 冻住的 worktree_base 会把主干 6 文件算进越界，已 `reset --hard` 撤掉未推送的 merge（仅现场）。仓外全量原文 sha256 `135b6869cf0ecdaf20e852d2a68d10763ea2d0485667caff1f7396198ad8fbe9`。
- 下一步唯一动作：`delegate scope-wait` seq=1 通过后改两处 fixture 并重跑失败用例。
