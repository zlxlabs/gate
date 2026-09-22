# OCR advisory 终态摘要

`gate-v2` 的 OCR advisory leg 有两条独立产物链：`review-shadow` 先写带当前身份的
`shadow-review-v1-{repository_id}-{head_sha}-{reviewer}-{run_id}-{run_attempt}.json`，随后
评论步骤尝试把 `advisory-comment-{reviewer}.md` 做 outbound scrub 并投递，最后写
`advisory-delivery-{reviewer}.json`。评论步骤依赖 workflow 自身 commit 的 scrub checkout，
所以 checkout 失败时不能用该步骤的成功与否推断审查是否产出。

`Record advisory terminal summary` 是 `always()` 的最后一个本地摘要步骤。它不读取评论正文、
错误文本或未经 scrub 的字段，也不依赖 `_gate-scrub-src`。它只接受文件名和 JSON 中的当前
`repository_id/head_sha/run_id/run_attempt/reviewer` 全部匹配的事件；缺失、非法或身份不符的
事件统一标为 `Review artifact: unavailable`，当前事件的 `status` 只允许 review-shadow
契约枚举。旧 run 的事件因文件名和身份校验不会被采信。

评论投递只从 `advisory-delivery-{reviewer}.json` 读取 `created`、`updated`、`not_created`
三个白名单值，并结合 checkout/comment step 的静态 outcome。诊断文件缺失或非法时摘要写
`Comment delivery: unknown`；因此「审查产物可用但评论未知/未创建」与「审查产物不可用」
始终分开。该步骤没有网络请求、重试或门禁裁决权，OCR 仍保持 advisory 非阻塞语义。
