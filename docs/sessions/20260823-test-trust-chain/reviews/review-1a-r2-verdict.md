# review-1a-r2 verdict — 运行时对抗矩阵

VERDICT: pass（本轮新增 P1 = 0）

审查范围：`3a7cd36283a35d18f9c3eaca8e1e83052fb45273..709b91ba554cdeaf909c2e515e8cb9152b60c02a`
（H0 冻结：进场 `git fetch origin card/gate-20260823-01` 后 `git rev-parse origin/card/gate-20260823-01`
= `709b91ba554cdeaf909c2e515e8cb9152b60c02a`，审查期间新提交不属于本轮）。
风险档：`personal`（P1 红线：数据丢失、静默出错、崩溃）；infra 类收敛按 internal 档。

## 本轮新证据声明

第 1 轮（cursor）已做：全量 diff 阅读、契约测试核对、base 红验、OCR 对照，结论 pass / P1=0。
本轮**不重复读 diff 找茬**；新证据 = **运行时行为矩阵的实际执行输出**——把
`.github/actions/diff-coverage-advisory/advisory.py` 当黑盒真跑：在 `/tmp/dca-r2/` 构造
10 个真实 git 仓（真实 `git init/commit/mv`、真实 coverage.py 产 lcov），逐格记录退出码、
stdout/stderr 与决策态；另以函数级 stub 验证分页/PATCH/stale-head 逻辑。此前任何轮次都
没有跑过 advisory.py 本体。

取证环境：advisory.py 取自 H0（连同 `scripts/scrub_outbound.py` 复制进
`/tmp/dca-r2/gate-root/` 复刻仓内真实路径布局，保证 `GATE_ROOT` import 链路与
GitHub Actions 上一致）；统一经 `uv run --with diff-cover python3 advisory.py` 调用
（与 `action.yml` 的 runner 分支一致）；每格设 `GITHUB_STEP_SUMMARY` 观察摘要落盘。
临时仓收工后清理（`/tmp/dca-r2`）。

## 运行时矩阵（逐格）

通用命令形态（每格仅替换仓目录与 SHA）：

```
cd <repo> && GH_TOKEN= PR_NUMBER=0 GITHUB_REPOSITORY= GITHUB_STEP_SUMMARY=<out> \
  uv run --with diff-cover python3 <gate-root>/.github/actions/diff-coverage-advisory/advisory.py \
  --base-sha 865fc746… --head-sha <head>
```

### 格 1：代码改动 + 覆盖部分命中的真实 lcov → 百分比，分子分母与手算一致

场景：base `src/calc.py` 仅 `add`；head 追加 `div`、`mul` 两个函数；测试只调 `div`。
`coverage run --source=src -m pytest` 产 lcov。

实际输出：

```
{"state": "covered", "percent_covered": 75, "covered_lines": 3, "total_lines": 4, ...}
EXIT=0
summary: - Note: `diff-coverage: 75% (3/4 changed lines)`
```

手算核对：diff 新增 8 行中可测行仅 4 行（`def div` / `return a / b` / `def mul` /
`return a * b`）；lcov 实测 `DA:5,1 DA:6,1 DA:9,1 DA:10,0`（`def mul` 因模块 import
被执行，计入已覆盖）→ 3/4 = 75%。**输出与手算逐位一致。判定：符合 spec 三态表行 1。**

### 格 2：代码改动 + 无 lcov → `no coverage data`，退出码 0

```
{"state": "no_data", "reason": "missing_lcov", ...}
EXIT=0
summary: - Status: `no_data`  - Note: `diff-coverage: no coverage data`
```

判定：符合 spec 三态表行 2 与「Never show 0% when data is missing」。

### 格 3：docs-only 改动（只改 README.md）→ 无注记（skip），退出码 0

```
{"state": "skip", "reason": "docs_only", ...}
EXIT=0
summary: - Status: `skipped`   （无 Note 行）
```

判定：符合 spec 三态表行 3；`render_note_line` 对 skip 返回 None，评论与摘要均无注记行。

### 格 4：改动只含无扩展名代码文件（Dockerfile、bin-script）→ 误判 docs-only，后果止于缺注记

```
{"state": "skip", "reason": "docs_only", ...}
EXIT=0
```

实际行为：确认 r1 F-4 预判——`CODE_EXTENSIONS` 不含无扩展名路径，Dockerfile/bin-script
被判为 docs-only。**后果边界实测确认：止于「缺注记」**，无崩溃、无错误百分比、退出码 0；
advisory 本就不参与门禁，personal 档可接受。维持 r1 backlog 判级，不因本轮实测升级
（P1 两问：① 真实使用可触发（PR 只改 Dockerfile）；② 后果 = 少一条 advisory 注记，
不改任何 job 结论，可接受 → 非 P1）。

### 格 5：空 lcov / 截断畸形 lcov → 不崩溃、不发错误百分比，退出码 0

5a 空文件：

```
{"state": "skip", "reason": "no_measurable_code_lines", ...}
EXIT=0
```

5b 畸形（截断的 `DA:` 记录 + 未闭合 `BRH`）：

```
::warning::diff-coverage advisory degraded to missing note: Command '[... '-m', 'diff_cover.diff_cover_tool', ...]' returned non-zero exit status 1.
EXIT=0
（summary 文件未写入）
```

判定：两条降级路径均不崩溃、不产出百分比；畸形 lcov 走顶层 `except Exception` →
warning + 恒 exit 0（I-1a 隔离成立）。diff-cover 的 stderr 被吞（r1 F-3 已登记 P3
backlog，本轮实测确认现象依旧，维持原判级不重复立案）。

### 格 6：base/head SHA 在仓中不存在（fetch 失败模拟）→ 降级无注记，退出码 0

6a 仓无 origin remote：

```
fatal: 'origin' does not appear to be a git repository
::warning::diff-coverage advisory degraded to missing note: Command '['git', 'fetch', '--no-tags', 'origin', 'deadbeef…', 'cafebabe…']' returned non-zero exit status 128.
EXIT=0
```

6b 有 origin 但 remote 不认识该 SHA：

```
fatal: remote error: upload-pack: not our ref cafebabe…
::warning::diff-coverage advisory degraded to missing note: ... exit status 128.
EXIT=0
```

判定：`ensure_review_commits` 失败经 `CalledProcessError` 上抛至顶层兜底，fail-loud
（git stderr 透传到 workflow 日志），降级为无注记，退出码 0。符合 spec「Base fetch」
条款的按需 fetch 语义与 I-1a 隔离。

### 格 7：GH_TOKEN 空 + PR_NUMBER=0 / API 不可达 → 退出码 0，无未捕获异常

7a 空 token + `PR_NUMBER=0`（即格 1 环境）：无任何 API 调用，正常出注记，`EXIT=0`。
7b 伪 token + `PR_NUMBER=1` + 真实仓库名（打真 api.github.com，401）：

```
{"state": "covered", "percent_covered": 75, ...}
::warning::could not update diff-coverage PR comment: HTTP Error 401: Unauthorized
EXIT=0
```

判定：度量结果、stdout JSON、step summary 均不受 API 失败影响；`HTTPError`
（`URLError` 子类）被捕获，仅损失评论。无未捕获异常。

### 格 8：混合改动（代码 + docs），lcov 只覆盖部分文件 → 分母只含代码改动行

场景：head 同时改 `src/calc.py`（+`mul`，已测）、`tests/test_calc.py`（+`test_mul`）、
`README.md`（+4 行 docs）。lcov 仅含 `src/calc.py`。

```
{"state": "covered", "percent_covered": 100, "covered_lines": 2, "total_lines": 2, ...}
- Note: `diff-coverage: 100% (2/2 changed lines)`
```

判定：`git diff --name-only` 实际涉及 3 文件（README.md 4 个新增行若进分母，
total 必 >2）；实测 `total_lines=2` 恰为 `src/calc.py` 的两个新增可测行——
**docs 行与非可测行不进分母**，I-1a「只度量改动行」运行时验证通过。

### 格 9：重命名文件（git mv + 小改）→ 不崩溃，记录实际行为

场景：`git mv src/calc.py src/calc2.py` + 追加 `mul`（未测）。lcov 按新路径产出。

```
{"state": "covered", "percent_covered": 75, "covered_lines": 3, "total_lines": 4, ...}
EXIT=0
```

实际行为：diff-cover 按「删旧 + 增新」处理（本例相似度 33% 低于 git 默认 rename
阈值），新文件的**未改动随迁行**（`def add` 等 2 行）也计入分母 → total=4 而非 2。
不崩溃、不变 no_data、百分比方向不反（covered 行仍算 covered）。后果：rename 场景
分母被随迁行稀释，advisory 数值偏保守/偏松皆有可能但永远落在真实行计数上。
**矩阵外 finding F-r2-1 见下。**

### 格 10：改动行全部未覆盖 → `0% (0/N)`，与 no data 可区分

场景：新增 `src/extra.py`（两个函数共 4 个可测行），测试完全不 import 它。

```
{"state": "covered", "percent_covered": 0, "covered_lines": 0, "total_lines": 4, ...}
- Note: `diff-coverage: 0% (0/4 changed lines)`
EXIT=0
```

判定：真 0 以 `state=covered, 0% (0/4)` 表达，与格 2 的 `state=no_data,
"no coverage data"` 在 JSON 与注记文本两层都可区分。符合「Never show 0% when data
is missing」的反向约束（有数据时必须敢显示 0%）。

## 分页 / PATCH（r1 F-2 修复）函数级 stub 验证

`/tmp/dca-r2/stub_test.py`：import H0 的 advisory.py，monkeypatch `_request` 记录调用，
四例全过：

```
[marker-on-page2] GET pages=[…page=1, …page=2] writes=[('PATCH', '…/issues/comments/777')]
[no-marker]       GET pages=[…page=1, …page=2] writes=[('POST', '…/issues/1/comments')]
::notice::skip stale diff-coverage result; head advanced
[stale-head]      GET pages=[] writes=[]
::notice::skip diff-coverage advisory; no note for this PR
[skip-state]      no requests issued
ALL PASS
```

判定：marker 落在第 2 页（首页 100 条无 marker）时正确翻页并 PATCH 既有评论、不发
重复 POST——r1 F-2 修复在运行时成立；head 前进则零写入；skip 态零请求。

## 矩阵外 finding

| ID | 判级 | 位置 | 违反 spec / 不变式 | P1 两问 | 说明 |
|---|---|---|---|---|---|
| F-r2-1 | P3 | `advisory.py:90-104`（经 diff-cover 行为） | 无法溯源 spec 条款（降一级）；spec 只承诺「changed executable lines」 | ① 触发：rename 且相似度低于 git 阈值或随迁行占比高；② 后果：advisory 分母含随迁未改行，百分比被稀释/抬升，但不改 job 结论、不产生虚构行 → 可接受 | 运行时格 9 实测：rename 按 delete+add 计，随迁未改行进分母。修法方向（如 diff-cover 对 rename 的处理或 `--diff-filter`）属增强，记 backlog。 |

矩阵 10 格 + stub 4 例均符合 spec 或落在 r1 已登记的降级路径上；**本轮无新增 P1，
无新增 P2**。

## 与 r1 的收敛关系

r1（diff 阅读视角）P1=0；本轮（运行时实测视角，换家换证据源）P1=0、仅新增 1 条 P3
→ infra/internal 档「连续 2 轮无新增 P1」收敛条件达成。

## 相关测试

H0 `709b91b` 临时克隆全量：
`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`
→ **637 passed in 15.34s**（与任务卡预期一致）。

## Backlog（本轮新增/维持）

- F-r2-1（P3，新增）：rename 场景分母含随迁未改行，度量精度问题，非正确性问题。
- 维持 r1  backlog：F-1（checkout sparse 逐验）、F-3（diff-cover stderr 被吞，
  本轮格 5b 实测复现）、F-4（无扩展名代码文件误判 docs-only，本轮格 4 实测确认后果
  止于缺注记）、F-5（非 PR 事件守卫）。
- blocking 阈值、E1/onboard：任务卡声明非本轮对象。
