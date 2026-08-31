# zlxlabs/gate PR #100 第 2 轮独立评审（对抗视角 + 修复增量专项审）

## 审查对象与结论

- 固定审查范围：`267eff0688c4ea5ad1253fae62e89e509b51322a..8b3e54e3d4f0ba2e8e22139508a2083403d14feb`
- 基线 SHA：`267eff0688c4ea5ad1253fae62e89e509b51322a`
- H0 SHA：`8b3e54e3d4f0ba2e8e22139508a2083403d14feb`
- 修复增量专项审范围：`645992c7..8b3e54e`
- 仓库风险等级：`personal`（AGENTS.md 第 3 行）
- 结论：**新增 0 条 P1；新增 1 条 P2（R2-F1，v1 部署形态下 STATE 评论永久闩锁）**。增量审四问全部通过。对抗六条中五条实测无缺陷，一条（第 6 条关联的部署形态核查）产出 R2-F1。
- 本轮新证据（区别于第 1 轮）：① 审查对象含 r1 之后新增的 4 个提交（`3a89637`、`5e9b539`、两个 merge commit）；② 实际构造并运行了两组黑盒探针（`/tmp/pr100-r2-probes.py`、`/tmp/pr100-r2-probe-advisory.py`，纯 /tmp 临时文件 + 内存 monkeypatch，未触碰任何 tracked 文件，`git status` 干净）；③ 核实了 v1/v2 两个 workflow 对 ledger action 的实际调用形态与 artifact 命名（第 1 轮只核实了 v2 的锁）；④ 联网核实了 GitHub concurrency 语义文档。

本轮只审上述固定 SHA 范围；未修改任何实现或测试文件。

## 第一步：修复增量专项审（`645992c7..8b3e54e`）四问

增量实际改动文件（`git diff --stat 645992c7..8b3e54e`）：`action.yml` description（5 行）、`docs/diff-coverage-advisory.md`（14 行）、r1 verdict 归档（131 行新增）、`tests/test_diff_coverage_advisory.py`（-10 行）、`tests/test_review_ledger.py`（+106/-约 10 行）。**生产实现文件零改动**。

1. **是否只修了登记在案的 findings？** 是。逐文件对账：`_same_repo_other_pr_entry`/`_other_repo_same_pr_entry` 互补噪声夹具 → F-1；`test_post_state_comment_skips_when_live_head_advanced` → F-2；`action.yml` description 与 `docs/diff-coverage-advisory.md` 措辞订正 → F-3；`test_diff_coverage_advisory.py` 删除 `hasattr(module, "_request")` 死分支与冗余 `writes` 断言 → F-4 / OCR 残留项；`inspect.getsource` 断言换成横跨渲染侧与发布侧的行为断言 → OCR-2/OCR-3。r1 verdict 文件归档是第 1 轮自己的完成条件产物，不算夹带。未发现未登记改动。
2. **是否新增了未经批准的抽象？** 否。生产代码零改动；新增的只是三个测试夹具 helper，每个都有 2 个以上真实调用点（四格矩阵、stale 用例、行为断言用例）。
3. **状态、事实源、fallback 是否被无依据增加？** 否。增量未触碰任何状态载体、判据来源或异常处理路径。
4. **是否留下了双路径？** 生产侧否。一处观察（不算 finding）：新的行为断言 `test_render_and_post_share_relevant_pr_entries_filter` 在测试内联复制了两键筛选表达式作为 oracle（`tests/test_review_ledger.py:1020-1024`），它锁死的是「两侧行为一致」而非「两侧字面调用同一函数」；鉴于行为断言在任一侧漂移时都会变红（含发布侧 skip 判定格），该等价强度足够，记录为观察。

增量审结论：**通过**，不按新增 P1 计。

## 第二步：对抗视角全量复验（六条逐条）

### 1. 误跳过（false skip）

- **构造了什么**：直接调用 `relevant_pr_entries` 喂入五类边界 entry：`pr_number` 为字符串 `"7"` vs 当前 `7`；entry 缺键 vs 当前正常；两侧都缺键（`None == None`）；`True` vs `1`；正常 int 对 int。另静态追踪 `main()` 里 `comments` 拉取异常被吞的路径（`build_ledger.py:861-866`）。
- **观察到什么**（探针实测输出）：`"7"` vs `7` → 不命中（relevant=0）；缺键 entry → 不命中；`None == None` → 命中；`True == 1` → 命中。
- **结论**：**生产可达路径下不存在误跳过**。`current` 由 `build_entry` 用 `--repository`（required 字符串）与 `--pr-number`（`argparse type=int`）构造，两键恒存在且类型恒为 str/int；entry 全部来自 `write_ledger` 的 JSON 序列化（int round-trip 保型）或评论游标的 base64 JSON（同为 int）。字符串/缺键/None 形态需要一个不写这两键的 producer，本仓不存在第二个 producer。`None == None` 与 `True == 1` 的宽松语义不可达，不构成本仓 personal 档的缺陷。唯一的失败放大点是 `fetch_comments` 异常与 `parse_state_entries` 合并在同一个 try 块里（`build_ledger.py:861-866`，pre-existing 结构）：评论拉取失败时游标历史不进 `prior_entries`，若 artifact 窗口同时丢史，relevant 计数退为 1 → 当轮 skip 掉本该发生的 PATCH。后果是当轮漏更新（评论陈旧一轮），下轮评论拉取恢复后自愈；该结构不在本 diff 内，且 diff 后此路径只会少发不会多发，记观察不记 finding。

### 2. 误创建（false create）

- **构造了什么**：静态分析 skip 判定的合取条件（`existing is None and len(relevant) <= 1`，`build_ledger.py:756-758`），枚举使其两侧同时为真的补集场景；探针模拟「首个 run 的 attempt 1 落 artifact、owner 手动 rerun 出 attempt 2」序列。
- **观察到什么**：relevant 集合恒含 current entry 自身（`main()` 传的是 `all_entries = dedupe([*prior, entry])`，`build_ledger.py:888-894`），故计数下限为 1，「≤1 跳过」恰好等价 spec 的「仅本轮 1 条」。attempt 2 场景：attempt 1 的 entry 经 artifact 读回后 relevant=2 → POST——符合 spec 表格「≥2 条 → POST」，attempt 1 本来就是历史。
- **结论**：diff 未引入误创建。唯一剩余的误创建路径是 pre-existing 的「`fetch_comments` 失败 → `existing=None` → 已有评论却 POST 重复评论」，且本 diff 让它在 relevant≤1 时从「必重复 POST」改善为「skip」，严格变好。记入 backlog B-1。

### 3. 游标自洽性

- **构造了什么**：探针模拟四轮序列——run1 无史跳过（断言零写请求）→ run2 读回 artifact 后 POST（解开 POST body 的 base64 游标，与 artifact 侧 relevant 比对）→ run3 既有评论 PATCH 抛异常（模拟 main 级吞掉）→ run4 用「artifact 全量 + 陈旧游标」恢复。
- **观察到什么**：run1 writes=`[]`；run2 恰好一次 POST，游标内容 `==` artifact 侧 `relevant[-20:]` 且 `== [e1, e2]`；run3 PATCH 异常上抛（生产中被 `build_ledger.py:896-897` 吞为 warning，exit 0）；run4 恰好一次 PATCH 到原评论 id 55，游标恢复为完整并集 4 条，与 artifact 侧完全一致。
- **结论**：未发现「不一致」型分歧。skip 轮的 entry 由当次 artifact 携带，不丢；评论游标对 artifact 只会**单向滞后**（评论缺最新若干轮），下一轮经 `dedupe_entries` 并集自动补齐，两源永不出现互相矛盾的两种历史。cursor 只存 relevant[-20:] 而 artifact 存全量是设计内的子集关系，不构成分歧。

### 4. 失败路径

- **构造了什么**：探针注入 GET live head 成功/PATCH 抛异常（第 3 条 run3/run4）；静态核对 GET live head 抛异常与 POST 抛异常两条路径的落点。
- **观察到什么**：三条失败路径全部收敛到同一形态——异常上抛至 `main()` 的 `try/except`（`build_ledger.py:891-897`），打 `::warning::`，exit 0；`write_ledger` 在 `post_state_comment` 之前完成（`:888-889`），所以**失败时 ledger artifact 已含本轮 entry、PR 评论未更新**：artifact 领先评论。GET live head 失败则连 stale 判定都不做，纯当轮空缺。
- **结论**：artifact 与评论的矛盾是单向、可恢复的滞后（见第 3 条 run4 实测恢复），不产生分叉历史；降级语义（warning 吞掉、退出码不变）与不变式 7 一致。无 finding。

### 5. 并发

- **构造了什么**：核实 `cancel-in-progress: false` 的真实语义与三处锁的实际分组；探针验证「两条 STATE 评论并存」时消费侧的选择一致性。
- **观察到什么**：
  - GitHub 官方语义：同一 concurrency group 任意时刻**最多 1 个 running**；`cancel-in-progress: false` 时新 run **排队**而非并行（[GitHub Docs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)：「there can be at most one running job or workflow in a concurrency group at any time」）。即第 1 轮的「排队」结论成立，不是「不取消但仍可并行」。
  - v2 的 ledger 锁是 repository 级（`gate-v2.yml:1098-1100`，group 无 PR 维度）→ 同仓所有 ledger run 串行，「两个 run 同时看到无 STATE + 2 条历史 → 双 POST」在 v2 内**不可达**：排队中的第二个 run 开始执行时重新拉取 comments，此时第一个 run 的 POST 已落地，走 PATCH。
  - v1（legacy `gate.yml:41-43`）是 workflow 级 per-PR 组 + `cancel-in-progress: true`，同 PR 不并行。
  - 探针：两条含 marker 的评论并存时，`parse_state_entries` 与 `post_state_comment` 的 `existing` 都取列表**第一条**（API 按创建时间升序 → 最旧），两侧选择一致；较新的重复评论成为永不读写的孤儿。
- **结论**：diff 引入的 skip 判定在两种部署形态下都被串行化保护，竞争窗口不可达。两个派生观察记 backlog：跨 v1+v2 双 caller 并存（迁移期误配置）可并发写同一 PR 的评论（B-2）；concurrency 默认只保留 1 个 pending，突发 push 下中间 pending run 被丢弃（B-3，属 workflow 既有设计，不在本 diff）。

### 6. advisory 侧反向

- **构造了什么**：① 静态扫描新 `advisory.py` 的全部 `os.environ` 读取与 `GH_TOKEN`/`PR_NUMBER`/`GITHUB_REPOSITORY` 字符串引用；② 动态探针：把旧版（`git show 267eff06:...advisory.py`，镜像目录树保证其 `GATE_ROOT` 导入可用）与新版 `main()` 在三组敌对 env 下对照运行，全程硬封网络（`urlopen` 与旧 `_request` 被打桩成抛 AssertionError），`measure` 打桩隔离。
- **观察到什么**：新版只读 `GITHUB_STEP_SUMMARY`，三个被删 env 零引用；旧版在正常 env 下确实进入评论路径（打桩 AssertionError 被触发），`PR_NUMBER=abc` 时旧版 `int()` 在 try 块外抛 `ValueError`、**未被捕获、非零退出崩溃**，新版同输入 exit 0；`PR_NUMBER` 为空时新旧均 exit 0。
- **结论**：没有任何输入使新版退出码或降级行为比 `267eff06` 更差；删除还顺带消灭了「env 畸形 → int() 崩溃非零退出」这条旧崩溃路径（GitHub 注入的 `github.event.pull_request.number` 恒为数字，该路径生产不可达，故只作观察）。不变式 7 保持。`action.yml` 仍声明 `token` input 并向下传三个 env（已无消费者），docs 已注明 token 为 caller 兼容保留（`docs/diff-coverage-advisory.md:42-43`），r1 backlog 已记，不重复报。

## Findings

### R2-F1 — P2：v1（legacy gate.yml）部署形态下，skip 守卫把「首轮延迟」变成「永久闩锁」

- **违反项**：spec 状态表「既有 STATE 评论=不存在 + 同 PR 历史 entries ≥ 2 条 → POST 创建（保住游标）」在 v1 形态下永不可达——spec 明文该游标「不能删」，而本 diff 在 v1 上的净效果等于对每个新 PR 删除游标。同时命中评审纪律的降层判据：守卫取值（artifact 历史计数）在一种实际部署形态下系统性失效。
- **具体失败场景**：下游仓以 `@<40hex>` pin 使用 legacy `gate.yml`（gate-v2.yml 头注明文「legacy gate.yml … continues to serve the existing fleet unmodified」）。fleet pin bump 到含本 PR 的 SHA 后，v1 仓任意**新 PR**：第 1 轮 `fetch_prior_entries` 按 artifact 名 `codex-review-ledger-v2` 精确匹配查询（`build_ledger.py:711`），而 v1 上传的 artifact 名是 `codex-review-ledger`（`gate.yml:415`，本 diff 未触碰，mismatch 为存量）→ 历史恒为空 → relevant=1 → skip；第 2、3…N 轮同样恒空、评论又因 skip 永不创建 → **永久闩锁，无自愈路径**（PR 正文披露的闩锁指望「artifact 窗口恢复」自愈，v1 上该窗口永远不存在）。后果：该 PR 每条 ledger entry 的 `comparison.kind` 恒为 `first_review`、`review_round` 恒为 1，跨 rerun 对照（persistent/resolved/new）全部丢失。
- **P1 两问**：会被触发吗——会，pin bump 后每个 v1 仓的每个新 PR 都触发。后果能否接受——后果类别（跨 rerun 对照丢失、不影响门禁判定与当次评审结果）与 PR 正文「已知限制」已接受者同类；**两问第二问过，不判 P1**。判 P2：机制与披露对象不同（披露的是 v2 上 artifact 列表延迟造成的**可自愈窗口**；本条是 v1 上 artifact 命名 mismatch 造成的**永久结构**），且 v1 是当前 fleet 的实际服役形态。
- **证据指针**：skip 守卫 `build_ledger.py:756-758`；artifact 查询名 `build_ledger.py:710-712`；v1 artifact 上传名 `gate.yml:413-415`；v1 ledger 步骤带 token 且 `if: always()`（`gate.yml:385-398`）；v2 上传名 `gate-v2.yml:1226`。
- **修复方向建议**（不属本卡执行范围）：`fetch_prior_entries` 同时查询两个 artifact 名，或 v1 上传名对齐 `-v2`，或接受并把 PR 正文「已知限制」段扩展到 v1 形态。

## 复核锚点（本轮 0 finding 以外的可复核事实）

- 目标测试实测：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_review_ledger.py tests/test_diff_coverage_advisory.py` → `192 passed in 2.22s`（被审 HEAD 原样，无任何改动）。
- 探针脚本为 /tmp 临时文件，未在仓内留下任何改动；`git status` 干净。
- spec 七条不变式逐条本轮均有对抗向验证（见六条逐答），其中不变式 1/2/3 由探针与现存测试（`tests/test_review_ledger.py:899-962` 四格矩阵 + 双键噪声、`:965-986` stale-head 用例）共同锁死；不变式 5 由固定范围 diff 逐字核验（`STATE_MARKER`/`STATE_RE`/`parse_state_entries`/base64 格式无改动）。

## Backlog / 越界观察

- **B-1**（pre-existing，diff 未恶化）：`fetch_comments` 失败时 `existing=None`，若 relevant≥2 会向已有 STATE 评论的 PR 重复 POST，产生一条永久 stale 的孤儿评论（消费侧一致选最旧，见第 5 条探针）。本 diff 反而把 relevant≤1 格从重复 POST 改善为 skip。
- **B-2**（迁移期误配置）：同仓同时挂 v1 caller 与 v2 caller 时，两条 workflow 的锁组互不重叠，可并发写同一 PR 的 STATE 评论 → 双 POST。需要 operator 错误配置才可到达，personal 档不追。
- **B-3**（workflow 既有设计，不在本 diff）：GitHub concurrency 默认只保留 1 个 pending run，新到 run 会挤掉排队中的旧 run（[changelog 2026-05-07](https://github.blog/changelog/2026-05-07-github-actions-concurrency-groups-now-allow-larger-queues/)）；突发 push 下被挤掉的 ledger run 当轮不落 artifact/评论。
- **B-4**（r1 backlog 已记，仅确认仍在）：advisory `action.yml` 的 `token` input 与 workflow 调用点 env 传递未清，属有意保留（caller 兼容），docs 已注明。

本轮没有修改任何实现或测试文件；探针均为 /tmp 临时脚本与内存打桩，未产生 tracked 文件改动。
