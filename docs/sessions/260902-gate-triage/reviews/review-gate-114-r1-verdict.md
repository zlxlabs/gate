verdict: pass

# gate#114 r1 独立全量评审 verdict

第 1 轮。方向 = 正向全量（PR 正文每句是否兑现：四值域、非法值归 unspecified 不抛、inferred_p1 交叉、空值默认、不新增函数、不改校验/渲染）+ 反向抽查（非 dict finding 的 `finding.get` 是否抛、与既有 `severity_counts` 是否同形；severity `"Major"` 大小写是否计入；ledger 行体积增长对下游有无影响）+ 熵增（两键 + 一段 Counter 循环）。风险档 **personal**（`AGENTS.md:3`）；纯统计投影，非失败路径，收敛按 personal（1 轮无新增 P1）。无 P1/P2/P3 finding。

## 本轮新证据

本轮是该 diff 的第一轮独立审查，证据不是「再读一遍同一份 diff」：

- H0 临时 worktree `/tmp/review-114` @ `5fc97177e76ba2578247a74c3e421073199b7672`：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_review_ledger.py` → **206 passed in 4.24s**（相对 base 的 review-ledger 测试集多 3 条新测试）。
- 本机 Python 3.12.3 对 H0 模块做反向探针：非 dict finding 抛 `AttributeError`；`"Major"` 不计入 `inferred_p1_count`；`unmeasurable` 自成桶；缺字段/空串/`None` 归 `unspecified` 不抛；jsonl 行增量约 51–98 字节。
- 只读 grep `/home/zlx/projects/personal/gate-hub/scripts` 与 `.../tests`：ledger 读入口按键 `.get`，无 review-summary 严格 schema。
- 卡面预取 OCR（minimax）`reviewed` 零 finding，本轮未重跑 OCR；结论不依赖该空数组。

审查对象冻结 `5123e3120ca6e9c4d84244528b74fe9346bd730c..5fc97177e76ba2578247a74c3e421073199b7672`。spec = PR #114 正文 + zlxlabs/gate-hub#581 第 4 条。已否决方案（把 `trigger_kind` 做成 ledger 必填校验；抽独立 `_finding_counts` 函数）不作为 finding 重提。

## Findings

无 P1 / P2 / P3 finding。下面按本轮方向列出已查项、对应 spec/不变式，以及为何不立 finding。

### 正向：契约核对

| 查过什么 | spec / 不变式 | 为何没问题 |
|---|---|---|
| 四值域 `measured \| inferred \| unmeasurable \| unspecified` | PR 正文 `trigger_kind_counts` 取值域 | H0 `_review_summary` 把合法三枚举原样累加，其余（缺字段、非字符串、不在集合内）改写为 `unspecified`。探针：`unmeasurable` 得到 `{'measured': 1, 'unmeasurable': 1}`，不并入 unspecified。 |
| 非法值归 unspecified、不抛 | PR 正文「缺字段、非字符串、不在枚举内一律 unspecified，不抛错」 | `test_review_summary_invalid_trigger_kind_counts_as_unspecified` 锁 `"guess"` 与 `1`；探针再锁 `""` / `None` / 缺字段 → `{'unspecified': 3}`，无异常。 |
| inferred_p1 交叉 | PR 正文：severity ∈ {blocker, major} 且 `trigger_kind == inferred`；gate-hub#581 第 4 条要 ledger 记下 inferred 的 P1 条数供后续统计 | `test_review_summary_inferred_p1_count_covers_blocker_and_major` 锁 blocker+inferred / major+inferred 计 2，minor+inferred 与 blocker+measured 不计。实现先规范化 kind 再交叉，非法 kind 不会误入 inferred。 |
| 空值默认 `{}` / `0` | PR 正文「空 findings 分别为 `{}` / `0`」 | 缺 audit 早退与「audit 在、findings=[]」两条路径都返回 `trigger_kind_counts: {}`、`inferred_p1_count: 0`。前者有测试断言，后者探针确认。 |
| 不新增函数 | PR 正文「不新增函数」；已否决 `_finding_counts` | `git diff ... -- build_ledger.py` 无 `+/-def`。计数写在 `_review_summary` 体内。 |
| 不改 finding 校验 / 评论渲染 / workflow / action.yml | PR 正文同句 | diff 仅 `build_ledger.py` + `tests/test_review_ledger.py`。评论/摘要用 `review['status']`、`review['finding_count']` 等具名键，不遍历 `review.keys()`，不加新键不会改 sticky comment / job summary 文案。 |
| #581 第 4 条「ledger 记录 trigger_kind」 | 供 `review-effectiveness.md` 统计 inferred P1 被反证的数量 | 本 PR 是投影出口：聚合计数 + 既有 `"result": audit.get("result")` 仍保留逐条 finding。与 gate-hub 侧 schema 无先后依赖（缺字段 → unspecified），符合 PR 正文。 |

### 反向抽查

**R1. `findings` 里某条不是 dict（字符串 / None）时 `finding.get` 会不会抛？与既有 `severity_counts` 是否同形？**

会抛，且与既有行为同形。H0 模块实测：

```
-- string finding --
THREW AttributeError: 'str' object has no attribute 'get'
-- None finding --
THREW AttributeError: 'NoneType' object has no attribute 'get'
-- mixed dict+string --
THREW AttributeError: 'str' object has no attribute 'get'
string severity generator THREW AttributeError: 'str' object has no attribute 'get'
None severity generator THREW AttributeError: 'NoneType' object has no attribute 'get'
```

既有 `"severity_counts": Counter(finding.get("severity", "unknown") for finding in findings)` 对同一输入同样 `AttributeError`。按卡面约束：既有行为同样会抛 → 记 backlog，不记 finding。`_compact_attempts` 对非 dict 是 `continue`，那是另一条列表、本 diff 未改。

- 工具标注：OCR 未报；本审查自查。
- 本仓判定：不立 finding（存量同形崩溃，非本 diff 引入）。
- 两问：①真实使用会被触发吗？生产 verdict schema 要求 `findings` items 为 object（gate-hub `verdict.json`），主路径不会塞字符串/None；本轮探针是合成输入。②触发了后果能否接受？会让 ledger job 崩，但这是改前就有的行为，本投影没有放大。

**R2. severity 为 `"Major"` 大小写时是否计入 `inferred_p1_count`？**

不计。探针：

```
severity_counts: {'BLOCKER': 1, 'Major': 1, 'blocker': 1, 'major': 1}
trigger_kind_counts: {'inferred': 4}
inferred_p1_count: 2
```

只有小写 `major` / `blocker` 进入交叉。这与 spec 字面 `{blocker, major}` 一致，也与既有 `severity_counts` 大小写敏感分桶一致。gate-hub `verdict.json` 的 severity 枚举本就是小写四值。

- 工具标注：OCR 未报。
- 本仓判定：不立 finding。
- 两问：①真实主审 finding 会写成 `Major` 吗？schema 枚举不允许；本轮用合成 `"Major"` 才打到这条分支。②不计 `Major` 会造成静默错/丢数据/崩溃吗？不会：合法输入仍计入；非法大小写在 `severity_counts` 里同样不并入 `major`。

**R3. ledger 行体积增长对下游有无影响？**

同一 `build_entry` 产物 `json.dumps(..., sort_keys=True)` 前后对比（删掉两新键模拟旧行）：

```
3 条 finding 的典型行：old 1271 B → new 1369 B，delta 98 B
缺 audit：old 745 B → new 796 B，delta 51 B
```

增量是两个短键（空默认 `"trigger_kind_counts": {}, "inferred_p1_count": 0`）。`write_ledger` 整行 dump，无行宽上限。评论渲染不序列化整个 `review` 对象。gate-hub 读入口 `.get` 忽略未知键。无消费者会因多 50–100 字节而拒读或截断。

- 工具标注：OCR 未报。
- 本仓判定：不立 finding。
- 两问：①现网 jsonl / artifact 会变大吗？会，每行几十字节。②后果能否接受？能。相对既有行（含完整 `result`）可忽略，且不改校验/渲染。

### 消费者核查（gate-hub scripts / tests）

只读 grep，确认无 review-summary 严格 schema。命中的生产读法：

```
scripts/review-ledger-report.py:131  finding_reported += int(review.get("finding_count", 0) or 0)
scripts/review-ledger-report.py:132  severity_counts.update(review.get("severity_counts") or {})
scripts/review-ledger-report.py:133  category_counts.update(review.get("category_counts") or {})
scripts/review-ledger-replay.py:243  severity = review.get("severity_counts") or {}
scripts/review-ledger-replay.py:248  findings = (review.get("result") or {}).get("findings") or []
```

测试夹具手写 `severity_counts` / `category_counts`，不校验「有且仅有这些键」。`tests/test_validate_verdict.py` 的 `additionalProperties: false` 约束的是 **verdict.json**（主审 finding 文档），不是 ledger 的 `review` 投影。`review-ledger-replay.py` 里的 `trigger_kind` 是 replay 自己的 `false_trigger/correct_trigger/...`，与 finding 字段同名不同对象。

结论：加 `trigger_kind_counts` / `inferred_p1_count` 不影响现网读入口。`docs/review-effectiveness.md` 尚未消费这两键——那是 #581 第 4 条的后续统计面，不是本 PR 的缺口。

### 熵增审查

对照 REFACTOR-guide 坏味道词表。已否决「抽 `_finding_counts`」。

| 新增项 | 是否熵 +1 | 判断依据 |
|---|---|---|
| `review.trigger_kind_counts` | 否 | 与既有 `severity_counts` / `category_counts` 同形的投影键，不是第二套事实源。生产者就是本函数；#581 第 4 条点名要这个统计出口。 |
| `review.inferred_p1_count` | 否 | 不能从两个边缘 Counter 反推交叉（inferred∩{blocker,major}），所以不是镜像。int 计数，无新类型/配置/开关。 |
| `for finding in findings:` + `Counter()` 循环 | 否 | 非法值要改写、还要做交叉，没法保持既有两行 generator 表达式而不抽 helper。已否决独立函数，循环是授权的最小形状。输出侧仍 `dict(sorted(...items()))`，与旁边两行 Counter 的对外形态一致。 |
| 测试 3 条 + 空值两条断言 | 否 | 只锁可观察投影，不引入产品运行面。 |

未新增文件、未新增函数、未改校验、未加 fallback。

## Backlog（存量 / 不阻塞）

- 非 dict finding 会让 `_review_summary` 在 `finding.get` 上崩，与既有 `severity_counts` / `finding_ids` 同形。生产 schema 要求 object；若将来要 fail-soft，应与那两行 generator 一起改，不单改本投影。
- 四值域里 `unmeasurable` 实现正确（探针已量），但 3 条新测试没锁这一桶。以后若有人从集合里删掉 `unmeasurable`，测试仍绿、该值会静默掉进 `unspecified`。覆盖宽度，不是当前行为错误。
- `inferred_p1_count` 与 `severity_counts` 一样大小写敏感；合法 verdict 枚举已是小写。
- gate-hub `review-effectiveness.md` 还没读这两键。本 PR 只做 ledger 投影，消费面是 #581 后续，不占本轮 finding。

## 结论

H0 兑现 PR #114 与 gate-hub#581 第 4 条的 ledger 侧出口：四值域计数、非法值归 unspecified 不抛、inferred×{blocker,major} 交叉、空默认 `{}`/`0`，且没有新函数、没有改校验或渲染。反向三条（非 dict 同形崩溃、`Major` 不计、行体积 +51–98 B）均已在 3.12.3 / H0 模块上量过，没有本 diff 引入的静默错 / 丢数据 / 崩溃。verdict：**pass**。
