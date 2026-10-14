# design — public ui-evidence 评论 action（gate-20261014-48，fixes gate#314）

## 目标
private gate-hub action 无法被 public 仓（herdweb）`uses:` 加载。在 public gate 新增
`.github/actions/ui-evidence-publish/`（composite action + sticky-comment.sh），从
gate-hub 同名 action **只读移植**：分页流合并（gh --paginate + jq slurp）、subject
marker、first-existing 单值 PATCH / 无匹配 POST、full 40 位 SHA 深链、fail-loud 全保留；
移除内网默认 URL、private 文档引用与内网措辞，evidence-base-url 改 required 无默认，
caller 显式传 URL。非目标：不迁移 caller（B 卡，待本 action 进远端 v2）、不处理媒体/上传、
不新增 workflow、不合 PR、不装依赖。

## public wrapper 必要性
GitHub 不能跨仓加载 private action → public gate 必须有自己的 action 本体。第二消费者：
官方 template 与 herdweb owner 直用 `zlxlabs/gate/.github/actions/ui-evidence-publish@v2`。

## 不变量（由 tests/test_ui_evidence_publish.py 锁定）
1. 空缺 URL 在任何 gh 请求前失败（stderr 标记 `UI-EVIDENCE-URL-MISSING`），无 URL 推断/fallback。
2. 只按 subject marker 单值更新：分页流 slurp 合并、重复 marker 取首个 id PATCH，无匹配才 POST；
   API/网络错误非零上抛，不误判「无旧评论」。
3. 评论正文如实反映 e2e success/failure/unknown（failure 不假报 success），深链含完整 40 位
   SHA 且与上传 title 同一 SHA；正文与 manifest 零内网主机、零私有默认。
