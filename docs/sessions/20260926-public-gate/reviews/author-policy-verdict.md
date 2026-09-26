# 公开仓作者与门禁终态审查

failure-visibility: clean

对象：`bb443ef2b8a8b428565fa69fdb3f0106ab6fa4d1`..`7760cf04f356b3e88bf5d743c7b04ffdb87cfd75`。结论：通过，无 P1/P2/P3。

规格 1：作者只取 `pull_request.user.login`。`evaluate` 必填 `pr_author`；缺失、非法 JSON、空串、非字符串都是 `unavailable`。探针里 `""`、`null`、`17`、`true`、`not-json` 和缺参均为 `unavailable`，实现不读 `GITHUB_ACTOR`。

规格 2：primary、resolve_advisory、shadow resolve 的 `if` 逐字节相同。聚合与 ledger 的 `REVIEW_EXPECTED`、`codex-expected` 与该式相同。Bot、fork、非法作者的 quality 走 `ubuntu-latest`。classifier 与 ledger 只检出 `job.workflow_sha`。disposition 审批人是 `github.actor`。

规格 3：ready Dependabot 在 fork、hosted、classifier 为 false 时是 `unavailable`；同一输入在基线上是 `expected_skip`。仅 payload 仍为 draft、primary 为 skipped、复核仍为 draft 才走旧 skip。复核已 ready 或复核失败为 `unavailable`。人类 fork/hosted 仍是 `expected_skip`，面板写明主审未跑。

规格 4：quality 作业没有 Silo 或飞书密钥。两个 caller 是具名密钥映射，没有 `secrets: inherit`。callee 的两项 Silo 密钥仍是 `required: false`。README 写明真实 Bot run 和 runner 隔离都未验证。

规格 5：代表测试在基线上 8 失败（未知参数 `pr_author`），在冻结头 16 通过。ledger、convergence、redline 夹具只补了作者参数，原断言未改。

OCR 信封：`status=partial`，profile `minimax`，model `MiniMax-M3`，findings 13，已逐条复核，不采纳。shadow 作业的 `if` 没有内联作者式，但其 `runs-on` 含同一守卫，且 resolve 的 `if` 与 primary 逐字节相同，当前不会把 Bot 或 fork 派到自建评审机。

unknown：本机没有执行真实 GitHub Actions 的 `toJSON`，也没有真实 Dependabot 或 fork 的 Actions run。仓声明 `risk-tier: personal`，本卡按 internal 复看；两条红线都未命中。
