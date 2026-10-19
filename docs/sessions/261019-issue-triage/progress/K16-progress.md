# K16-progress

## 2026-10-19 · 修前扫描与第三支收窄

- 当前阶段：implementing
- 本段结论：agent-config `origin/main` `2f7e7fb17ee3eb53d479be3c8272e42912961f2e` 上 TOKEN 第三支实测命中 20 行 / 18 文件（不是卡面写的 13 行 / 11 文件）。判据定为「混合大小写，或含 base64 附加符 `+/=`」；kebab / 单大小写标识符不命中。前两支保持原样。
- 关键决策与已否决方案：否决「字母+数字即命中」——`fake-token-setenv-181-not-a-real-secret` 这类带数字的 kebab 仍会误报，修后无法让主干清单清零。仓外证据：`~/.local/state/gate-hub/261019-issue-triage/K16-agent-config-main-third-branch-pre.json` sha256 `8d6ae28b8a6a5c1ce3b13a9e50086a4d142ae651fbcd599d94d2f0bc7a702f49`。
- 下一步唯一动作：跑 `tests/test_scrub_outbound.py`，绿则提交本段。
