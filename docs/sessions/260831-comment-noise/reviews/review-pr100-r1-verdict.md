# zlxlabs/gate PR #100 第 1 轮独立评审

## 审查对象与结论

- 固定审查范围：`267eff0688c4ea5ad1253fae62e89e509b51322a..645992c7af3216ba599987df0213faff7644b567`
- 基线 SHA：`267eff0688c4ea5ad1253fae62e89e509b51322a`
- H0 SHA：`645992c7af3216ba599987df0213faff7644b567`
- 仓库风险等级：`personal`
- 结论：P1 为 0；发现 2 条 P2 测试契约缺口、2 条 P3 清理/文档问题。
- P1 判定：当前实现未触发数据丢失、静默错误或崩溃；P2/P3 不阻塞 personal 档合并，但应由主脑分诊。

本轮只审上述固定 SHA 范围，不纳入后续提交，也没有修改实现文件或测试文件。

## 审查方法与证据

实际执行了以下检查：

- 读取 `review-discipline/SKILL.md`、风险分级表和 `REFACTOR-guide.md`；按 infra/状态机要求完成正向全量审、降层三问、测试约束力与熵增审查。
- OCR 前置扫描返回 `status=partial`，MiniMax 主腿给出 5 个低严重度候选，其中 1 个确认、1 个反驳、3 个未复核。工具意见未直接作为结论；确认的 `_request` 测试残留纳入 F-4。
- 目标测试：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_diff_coverage_advisory.py tests/test_review_ledger.py`，结果 `191 passed in 4.08s`。
- workflow 锁测试：2 passed，确认 `ledger` 使用 repository 级 `cancel-in-progress: false`，且无 workflow-level concurrency。
- stale 黑盒探针：live head 与事件 head 不同，实际方法序列为 `['GET']`，写请求为 `[]`，并打印 stale notice。
- 测试变异探针：把 `relevant_pr_entries` 在内存中改成只按 repository 或只按 pr_number，现有四格仍全部通过；未写入任何 tracked 文件。
- 删除面扫描：确认 advisory 生产模块不再包含旧评论发布符号；发现 active action metadata、设计文档和修改后的测试中仍有旧契约/旧符号残留，详见 findings。

## 逐不变式核验

### 1. 缺失 STATE 评论且同 PR 条数不超过 1 时零 HTTP 写请求

- 代码在哪：`.github/actions/review-ledger/build_ledger.py:751-762` 先读取 live PR head，再在 `existing is None` 且相关条数 `<= 1` 时直接 notice 返回；只有后续两处才传入 `method="PATCH"` 或 `method="POST"`。`_api_json` 在 `:706-707` 统一委托 `_api_request`，因此不会绕过记录器。
- 哪个测试锁死：`tests/test_review_ledger.py:879-942` 的 `no_comment_one_entry` 分支断言写方法为空；`tests/test_diff_coverage_advisory.py:233-285` 断言 advisory 没有 GitHub 写请求并仍写入 summary。
- 结论：实现满足“零 POST/PATCH”，不是仅吞异常。现有 ledger 测试能覆盖当前 `_api_request` 写路径，但其筛选夹具有双键盲区，见 F-1。

### 2. 条数必须按同 repository 且同 pr_number 计算

- 代码在哪：`.github/actions/review-ledger/build_ledger.py:112-117` 同时比较 `repository` 与 `pr_number`；渲染侧 `:120-124` 与发布侧 `:755-762` 共用该筛选口径。
- 哪个测试锁死：`tests/test_review_ledger.py:894-942` 有四格发布矩阵，且 `:898-900` 注入一个无关 entry；`tests/test_review_ledger.py:945-948` 检查两个生产调用方都使用 helper。
- 结论：当前生产代码正确，但测试中的无关 entry 同时改变了两个键，不能证明两个条件必须同时成立。只按单键的变异实现仍能让四格通过，违反测试约束力要求，见 F-1。

### 3. stale-head 守卫仍在写入前且优先级不变

- 代码在哪：`.github/actions/review-ledger/build_ledger.py:751-754` 在检查 STATE 评论和两个写分支之前读取 live PR head；不匹配时返回。
- 哪个测试锁死：没有专门的 stale-head 回归测试。现有 `tests/test_review_ledger.py:904-908` 的 fake API 总是返回与事件 head 相同的值，只覆盖正常 head。
- 结论：代码顺序正确；黑盒探针在 live head 前进时只产生 GET、无写请求且打印 stale notice，但测试没有锁死这一不变式，见 F-2。

### 4. 已存在评论必须 PATCH，不能删后重建

- 代码在哪：`.github/actions/review-ledger/build_ledger.py:755-762` 先定位 `STATE_MARKER`，存在时只调用评论 ID 的 `PATCH`；不会走 `POST`。
- 哪个测试锁死：`tests/test_review_ledger.py:879-942` 的 `has_comment_one_entry` 和 `has_comment_two_entries` 均要求一个 `PATCH`、指定评论 endpoint，并断言没有 `POST`；`tests/test_review_ledger.py:802-842` 还验证 PATCH 正文经过 scrub。
- 结论：通过。现有评论不会被删除或重建。

### 5. STATE_MARKER、STATE_RE、base64 格式、parse_state_entries 函数体保持不变

- 代码在哪：`.github/actions/review-ledger/build_ledger.py:50-51` 的 `STATE_MARKER`/`STATE_RE` 与 `:94-109` 的 `parse_state_entries` 在固定范围 diff 中没有改动；新增 helper 只替换了 render 中的等价筛选表达式。
- 哪个测试锁死：`tests/test_review_ledger.py:670-686` 验证旧布局的 v2 游标仍可读；`:763-779` 验证 v1 不被恢复且输出 v2；`:700-742` 验证 base64 游标位于末尾并可还原 entry 列表。
- 结论：通过。逐字不变由固定范围 diff 核验，行为兼容由上述测试锁死。

### 6. 不引入新的持久化介质

- 代码在哪：固定范围只新增 `relevant_pr_entries` 和一个发布 guard；没有新增 check run、git ref 或 artifact。现有 workflow 仍在 `.github/workflows/gate-v2.yml:1223-1229` 上传 `codex-review-ledger-v2`。
- 哪个测试锁死：`tests/test_review_ledger.py:932-939` 验证 POST 正文仍含 v2 cursor；固定范围 `git diff --name-only` 只列出两个实现文件和两个测试文件，没有 workflow 或新的持久化文件。
- 结论：通过。没有新状态载体或新 artifact。

### 7. 两个模块的降级语义、warning 和退出码保持不变

- 代码在哪：advisory 的 measure/summary 与异常处理保留在 `.github/actions/diff-coverage-advisory/advisory.py:178-220`；ledger 主流程仍在 `:890-898` 捕获评论更新异常并打印 warning，最后 `:901` 返回 0。改动只在评论创建前增加返回分支。
- 哪个测试锁死：`tests/test_diff_coverage_advisory.py:181-194` 验证 measure 失败返回 0；`:197-222` 覆盖 skip/no_data/covered summary；`tests/test_review_ledger.py:782-799` 验证 scrub 失败不会产生 API 调用。
- 结论：通过。删除 advisory 评论路径后，summary 仍是唯一发布面；ledger 的新 skip 是显式 notice 加零写请求，不改变异常降级出口。

## 降层三问

### ① 终态写入成功之前已经发生哪些不可逆动作？

advisory 在 `measure` 后只追加本地 `GITHUB_STEP_SUMMARY` 文件，没有 PR 评论、通知或其他外部写入。ledger 在 `post_state_comment` 中先渲染/脱敏、做 artifact/comment/head 的 GET，再通过 stale guard 和 history guard；在 guard 之前没有外部不可逆写入。主流程的 `write_ledger`（`build_ledger.py:888-889`）是本地临时文件写入，workflow 的 artifact 上传在 action 返回后进行。真正会发邮件的 PR `POST`/`PATCH` 只发生在 `:760-762`，且只有允许写入的分支到达这里。

### ② 守卫值在实际部署形态下可靠吗？同一 PR 并发会怎样？

相关 entry 按 artifact 中的 `repository`/`pr_number` 与当前 entry 比较，评论列表 API 本身限定在目标 PR，live head 由 PR API 返回。当前 gate-v2 的 ledger job 在 `.github/workflows/gate-v2.yml:1094-1100` 使用 repository 级 `cancel-in-progress: false` 锁，同仓并发 ledger 会排队而不是同时读空状态再 POST；对应锁由 `tests/test_gate_v2_contract.py:421-444` 固定。quality 的 advisory 也在 per-PR job lock 内运行。artifact 可见性延迟造成的首轮闩锁属于任务已披露限制，本轮不重复报；我没有发现其后果超出“丢跨 rerun 对照、不影响门禁判定和当次结果”。

### ③ 保护覆盖的是“写入”还是“行为”？

advisory 的实现删除了 PR 评论发布行为，summary 仍保留。ledger 的新 guard 覆盖的是评论创建写入：没有 STATE 且相关历史不超过一条时，确实不调用任何 HTTP 写方法；已有 STATE 的 PATCH 行为和历史足够时的 POST 行为保留。GET、artifact 本地写入和 summary 追加仍会发生，这与“零 HTTP 写请求”的目标并不冲突。

## Findings

### F-1 — P2：四格夹具没有独立验证 repository 与 pr_number 两个维度

- 违反项：不变式 2；评审要求“判定用同 repository + 同 pr_number 的条数”。
- 具体失败场景：当前 PR 为 `repository=zlxlabs/app, pr_number=7`，entries 为一条 `(zlxlabs/app, 7)` 和一条 `(zlxlabs/app, 99)`，无 STATE 评论。正确结果是相关条数 1、只打印 notice、零 POST/PATCH。若实现退化成只按 repository 过滤，它会把两条算作相关并 POST；只按 pr_number 过滤时，输入 `(other/repo, 7)` 也会得到同样错误。当前夹具的无关 entry 是 `(other/repo, 99)`，两种错误实现都仍会通过四格。
- 证据指针：`tests/test_review_ledger.py:865-876` 构造无关 entry，`:898-900` 将其放入矩阵；`:945-948` 只做源码调用存在性检查。内存变异探针显示 repository-only 与 pr-only 两种退化实现均 `all_four_expected=True`。
- 严重度说明：当前生产 helper 在 `.github/actions/review-ledger/build_ledger.py:112-117` 是正确的；这是会放过静默多发评论回归的测试缺口，未在当前代码中触发 personal P1，因此判 P2。

### F-2 — P2：stale-head 写入前守卫没有回归测试

- 违反项：不变式 3；stale-head 守卫必须仍在最前并优先于任何写入。
- 具体失败场景：事件携带 `head_sha=event-head`，运行时 PR 已前进为 `live-new-head`，无 STATE 评论但同 PR 有至少两条历史 entry。若后续修改把 stale 检查移到 POST 之后或删除它，该场景会向 PR 发布旧 head 的 state comment 并触发通知邮件；当前新增四格 fake API 在 `tests/test_review_ledger.py:904-908` 总是返回同一个 head，无法变红。
- 证据指针：正确守卫在 `.github/actions/review-ledger/build_ledger.py:751-754`；写入分支在 `:755-762`；现有测试矩阵调用在 `tests/test_review_ledger.py:911-916`，没有 live-head 不一致用例。独立黑盒探针观测当前代码为 `methods=['GET']`, `writes=[]`, stale notice=true。
- 严重度说明：当前代码顺序正确，缺陷是回归约束缺失；没有当前 P1 触发，判 P2。

### F-3 — P3：active action metadata 与文档仍承诺 PR 评论

- 违反项：改动 1 的“job summary 成为唯一发布面”契约；同时命中删除面核验中对 action metadata/docs 漂移按 P3 记录的要求。
- 具体失败场景：新消费者根据 action metadata 或当前 advisory 文档调用 action，并在 PR 评论中寻找 diff-coverage 注记；实际 `.github/actions/diff-coverage-advisory/advisory.py:198-220` 只写 summary，不再创建或更新 PR 评论，于是消费者按文档找不到承诺的产物。
- 证据指针：`.github/actions/diff-coverage-advisory/action.yml:3-4` 仍写 “Post ... on the PR”；`docs/diff-coverage-advisory.md:3-5` 仍写 Gate v2 posts PR comment，`:21-25` 表格列为 PR comment，`:38-39` 仍说下游解析 PR comments。
- 严重度说明：会造成当前契约误导，但不改变门禁结果、不丢数据、不崩溃，personal 档不命中 P1，判 P3。

### F-4 — P3：修改后的 advisory 测试仍引用已删除的 `_request` 路径

- 违反项：删除面核验要求 advisory 删除 `post_sticky_comment`/`render_comment`/`MARKER`/`_request` 后不留引用；这不是当前运行时行为缺陷，但属于本次 diff 的残留测试路径。
- 具体失败场景：当前 advisory 模块没有 `_request`，所以 `tests/test_diff_coverage_advisory.py:248-255` 的 `if hasattr(module, "_request")` 分支永远不执行；测试表面上安装了禁止调用的拦截器，实际并未覆盖该路径。若旧 `_request` 因后续误改重新出现，这段 lambda 还会先向 `requests` 追加伪记录再抛错，造成测试观测噪声。
- 证据指针：`.github/actions/diff-coverage-advisory/advisory.py:1-220` 已无 `_request` 定义；`tests/test_diff_coverage_advisory.py:225-230` 的源码负断言是有意的存活契约检查，但 `:248-255` 是对已删运行时符号的条件引用。OCR 对该残留标注 low/confirmed；独立源码核验确认当前分支不可达。
- 严重度说明：生产行为不受影响，且源码负断言仍有效；这是死测试分支和删除不彻底，判 P3。

## 熵增审查

`relevant_pr_entries()` 不是熵 +1：它有两个真实生产消费者（render `:120-124`、post `:755-762`），承载的是本次 spec 明确要求共享的筛选口径，并删除了原 render 侧的内联镜像。没有新增持久化状态、配置项或单实现接口；本维度无额外 finding。

## 删除面核验

- advisory 生产模块中的 `post_sticky_comment`、`render_comment`、`MARKER`、`_request` 和 Issues comments URL 已删除。
- `tests/test_diff_coverage_advisory.py:228-230` 对已删符号的字符串检查是故意验证“源码不存在”，不作为残留；`:248-255` 的条件 monkeypatch 是 F-4。
- `tests/test_pr_size_preflight.py` 中同名 `render_comment`/`post_sticky_comment` 属于另一个 action，不是 advisory 删除符号，不计 finding。
- 历史 review verdict 中关于旧 advisory 评论路径的文字是归档事实，不作为 active 契约；当前 active `action.yml`/`docs/diff-coverage-advisory.md` 漂移已计入 F-3。

## Backlog / 越界观察

- 已披露的 artifact 列表延迟导致首轮 state comment 闩锁，本轮按任务要求不重复报；实测其描述的后果仍限于跨 rerun 对照数据，不影响门禁判定或当次评审结果。
- `fetch_prior_entries` 的既有 artifact 查询仍受原有可见性/数量窗口约束；这是本次改动前已存在的历史来源限制，未将其冒充为本轮新 finding。
- workflow 的 advisory action 仍保留不再使用的 token/PR 环境输入，属于与 F-3 同一 active metadata 清理面；没有另列重复 finding。

本轮没有修改测试或实现文件；未运行仓库全量测试套件，按任务约定仅运行目标测试和并发契约测试。
