<!-- delegate-outcome: succeeded -->

# PR317 public UI evidence action — R1 verdict

## 范围与结论

- 冻结审查范围：`008170e11a949c77029d1dbb658e36554b2c8b7b..9d05e7d64311c4dc1a5387606cb96b5247d16678`；8 个 diff 文件均已审。
- `failure-visibility: p2-only`
- 评审结论：1 项 P2 测试覆盖缺口；无确认的 P1。
- 严重度：P2。交付阻断：否；当前 POST 错误实际仍以非零退出，但对应测试没有走到 POST 分支。

## Finding

1. `tests/test_ui_evidence_publish.py:213-219` 的 `fail="post"` 用例在第 216 行仍提供带 marker 的 id 42。生产脚本因此走 `sticky-comment.sh:49-53` 的 PATCH，而非 POST；参数名虽是 `post`，并未锁定 POST 错误上抛（Spec 2）。实测该夹具调用 `GET, PATCH`；无 marker 夹具才调用 `GET, POST`，两者当前均以 rc=1 退出。实际无 marker 且 POST 失败可触发；当前命令受 `set -euo pipefail` 保护，不会假报成功，故为 P2、非交付阻断。

## 不变量定位

- Spec 1：`action.yml:13-42` 锁 required/default、PR 条件与 env 映射；`sticky-comment.sh:9-14` 在 gh 请求前拒绝缺失/空 URL；测试 `test_ui_evidence_publish.py:103-137` 覆盖 manifest 与 URL 失败无调用。
- Spec 2：`sticky-comment.sh:24-57` 保留真实状态、`--paginate | jq -s`、首个 marker 单值 PATCH / 无匹配 POST、full SHA 深链及 fail-loud；测试 `test_ui_evidence_publish.py:140-219` 覆盖实际 bash、系统 jq、多页数组 fixture、body bytes、GET/PATCH 错误。POST 错误覆盖见上述 finding。
- Spec 3：action/script 只发评论，不碰媒体/上传、不装依赖/重试/新增状态；`tests/test_no_runtime_install.py:57-60,108-114` 登记 action 与 shell 入口并保留闭集扫描；README:259-271 写明 caller 预装 bash/gh/jq。
- Spec 4：README:263-270 与 `design.md:9-14` 约束先上传、SHA 同源及 v2 发布后迁移；远端 `v2` 当前仍指向 base。没有验证真实 caller、Set up job 或 TLS，符合本卡边界。
- 规模：新增生产入口为 43 行 manifest + 57 行脚本；测试 helper 被多用例复用，无额外运行态抽象或依赖。其余新增为接口/进度文档与精确入口登记，未见无第二消费者的泛化。

## 复验与边界

- 窄测：`UV_PROJECT_ENVIRONMENT=/tmp/herdweb-ui-r1-49/verify-venv uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_ui_evidence_publish.py tests/test_no_runtime_install.py` → `18 passed in 0.86s`；系统 jq 为 1.7。
- Base 红验：scratch 绑定 `008170e...`、仅拷新增测试，目标测试以 AssertionError 红；失败点是明确的 `SCRIPT.exists()` 缺文件守卫，故只证明新入口缺失，不作为语义约束力证据。scratch 自有 `.venv`，已清理。
- 变异红验：scratch 中只将 URL 检查 `${EVIDENCE_BASE_URL:?}` 改成 `${EVIDENCE_BASE_URL?}`；空串用例实际发出 POST 并 rc=0，断言 `url='' 却退出 0` 红。保存的单行 diff/输出在 `/tmp/herdweb-ui-r1-49/`；变异行已恢复，scratch diff 干净。
- OCR：`reviewed_fallback`（DeepSeek v4 flash）；主腿 `content_error:all_file_reviews_failed`，备腿完成。4 条候选已逐项复核；未采纳项分别依赖未接入 caller 的并发场景、未证实的评论作者权限、违约 SHA 输入或未约定的多重末尾斜杠。
- 卡面主干基线为 unavailable，继承红未能判定；本地只跑了指定窄测，无真实 GH 请求、无新增测试。原实现树未出现在仓库 worktree 清单；主树与 lead 树在复核前均 clean，无法声称单独原实现树的状态。
- 踩坑：base 红验停在存在性断言；判据变异则确实触发空 URL 静默成功路径并被测试抓红。此次未新增测试。远端 v2 / review 分支和提交记录见完整派发报告。
