#!/usr/bin/env bash
# 在 PR 上发/更新一条固定 marker 的粘性评论:如实汇报 e2e 成败,深链到证据服务器按
# commit SHA 的搜索结果。不产出/搬运任何文件;caller runner 需预装 bash/gh/jq。
# public 版(private gate-hub action 移植):URL 无默认/fallback,缺了就在任何 gh
# 请求前失败;不引用 private 部署文档。
set -euo pipefail

SUBJECT="${SUBJECT:-UI}"
HEAD_SHA="${HEAD_SHA:?HEAD_SHA is required (must match the evidence report title SHA)}"
# required input 之外脚本侧再校验一次:URL 缺失必须在任何 gh 请求前失败。
: "${EVIDENCE_BASE_URL:?'UI-EVIDENCE-URL-MISSING: evidence-base-url is required; caller must pass the explicit evidence URL'}"
# 去掉 caller 可能传入的末尾斜杠——不归一化的话 `https://host:8443/` 会拼出
# `https://host:8443//reports?...` 双斜杠,证据站深链 404/重定向异常。
EVIDENCE_BASE_URL="${EVIDENCE_BASE_URL%/}"
MARKER="<!-- ui-evidence-bot:${SUBJECT} -->"
REPO="${GITHUB_REPOSITORY:?}"
PR="${PR_NUMBER:?}"
STATUS="${E2E_STATUS:-unknown}"
SHORT_SHA="${HEAD_SHA:0:7}"
# 深链按报告 title 里的完整 head SHA 命中搜索结果;caller 上传报告时的 title 必须用
# 同一个 SHA,否则搜不到。
EVIDENCE_SEARCH_URL="${EVIDENCE_BASE_URL}/reports?search=${HEAD_SHA}"

case "$STATUS" in
  success) STATUS_ICON="✅ 通过" ;;
  # 措辞不预设 report/trace 已生成:e2e 在更早阶段(起服务/装依赖)失败时可能没有产物,
  # 如实反映,不过度承诺。
  failure) STATUS_ICON="❌ 失败(若已跑到 e2e 阶段才有报告;失败在更早阶段时可能未生成)" ;;
  *)       STATUS_ICON="⚪️ ${STATUS}" ;;
esac

# ⚠️ body 是双引号串,里面的反引号必须 \` 转义,否则会被 bash 当命令替换执行 → exit 126。
body="$MARKER
### 🎬 ${SUBJECT} 走查证据

- e2e 状态:**${STATUS_ICON}**(check 如实反映本次走查结果)
- 🔎 证据站:[按本次 commit 搜索 ↗](${EVIDENCE_SEARCH_URL}) —— 报告 title 含完整 SHA;搜不到就手动到 Reports 页粘贴 SHA:\`${SHORT_SHA}\`"

# 已有粘性评论则 PATCH,否则新建。
# ⚠️ 三处易错点(移植自 private 版实测结论,别倒退):
#   1. --paginate 拉全部页——PR 评论数超过单页时 marker 评论可能落在第 2+ 页,
#      不分页会导致「找不到旧评论 → 一直新建」。
#   2. `jq -s`(slurp)把多页合并成一个数组再展平(`.[][]`)——`--paginate` 对列表接口
#      把每页各自的 JSON 数组裸拼在 stdout(不是一个合并数组);不 slurp 会每页各输出一个
#      候选 id,PATCH 目标 URL 变成非法的多值拼接。
#   3. MARKER 通过 jq --arg 传值,不拼进 jq 过滤器源码——SUBJECT 含引号/反斜杠不会破坏
#      表达式;且不吞掉 gh api 的失败(没有 `2>/dev/null`/`|| true`),鉴权/网络错误随
#      `set -euo pipefail` 非零上抛,不会被误判成「没有旧评论」。
existing="$(gh api --paginate "repos/$REPO/issues/$PR/comments" \
  | jq -s -r --arg marker "$MARKER" '[.[][] | select(.body | contains($marker))] | .[0].id // empty')"
if [ -n "$existing" ]; then
  gh api --method PATCH "repos/$REPO/issues/comments/$existing" -f body="$body" >/dev/null
  echo "updated comment #$existing"
else
  gh api --method POST "repos/$REPO/issues/$PR/comments" -f body="$body" >/dev/null
  echo "created comment"
fi
