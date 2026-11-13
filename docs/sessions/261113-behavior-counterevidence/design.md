# DESIGN-note：disposition 支持可重放的行为反证（gate#404）

门槛四条按用户 2026-11-12 裁决，不放宽。本卡只定契约与落点，不改 workflow。

字面量反证（`git grep -n -F -e <literal> <sha> -- <path>`）继续有效；本设计只给「逻辑层误报、grep 表达不了」加第二条证据类型。

## 非目标

- 不改现有九段 argv 白名单，不把 pytest 写进 `counterevidence_argv.py`。
- 不在 `git-source-service` 作业里执行调用方测试代码。
- 不接受粘贴日志当重放。
- 不把语义分歧或覆盖缺口收成机器关闭。

## 1. 证据 schema

### 现状（实测）

- 反证 argv 契约版本化在 `4607ae7`：`.github/actions/gate-aggregator/counterevidence_argv.py:12` 导出 `COUNTEREVIDENCE_ARGV_CONTRACT_VERSION = 1`，只认九段 `git grep`（同文件 :15–19；设计见 `docs/sessions/261017-argv-contract/design.md`）。模块不做 I/O。
- 回执 schema 在 `.github/actions/gate-aggregator/convergence.py:48`：`DISPOSITION_RECEIPT_SCHEMA_VERSION = 3`。`_counterevidence_reason`（:1332–1383）要求 `command/output/result/pointer` 均为非空字符串，`result=="refuted"`，且 `gate_rerun.argv` 通过 argv v1。
- 生产者 `.github/actions/gate-disposition/issue_receipt.py:29` 的 `COUNTEREVIDENCE_FIELDS` 同这四键；:77–110 用 `shlex.split(command)` 后 `subprocess.run` 复跑 grep，超时 `COUNTEREVIDENCE_RERUN_TIMEOUT_S=30`（convergence.py:54）。
- `40b9de9` 给字面量加了 `match_expectation=absent`，仍走同一 argv。
- workflow 入口 `.github/workflows/gate-v2-disposition.yml:12` / `:23` 仍把 `counterevidence_json` 描述成「command, output, result=refuted, pointer」。
- 调用方 `templates/caller-gate-disposition.yml` 原样转发该字符串，无第二输入。
- agent-config `scripts/git/gate_disposition.py:44-46` 的 `COUNTEREVIDENCE_COMMAND_RE` 与 `:194-239` 的本地预检只认九段 grep；不合规本地 Fail，避免空跑 workflow。

### 方案：并行契约，不抬 argv 版本

行为反证不是另一种 argv，抬 `COUNTEREVIDENCE_ARGV_CONTRACT_VERSION` 会逼所有字面量向量改版。仿 `4607ae7` 抽平行模块：

- `.github/actions/gate-aggregator/behavior_counterevidence.py`
- `BEHAVIOR_COUNTEREVIDENCE_CONTRACT_VERSION = 1`
- 邻接 `behavior_counterevidence_v1_vectors.json`
- 纯句法校验，不跑测试、不读仓

`counterevidence_json` 增加判别字段 `kind`：

| `kind` | 含义 | 缺省 |
|---|---|---|
| 省略 / `"literal_grep"` | 今日 argv v1 | 兼容存量回执 |
| `"behavior_replay"` | 本设计 | 新路径 |

不抬 `DISPOSITION_RECEIPT_SCHEMA_VERSION`。[推断] 调用方钉的是同一 gate SHA，生产者与消费方一起换；旧聚合器碰到无 `command` 的 JSON 会走现有 `counterevidence_required`，fail-closed。

`behavior_replay` 作者提交字段（机器重放结果由 gate 回填，作者不得贴日志）：

- `kind`（恒 `behavior_replay`）
- `result`（恒 `refuted`）
- `pointer`（人读定位，非判定依据）
- `finding_id`（必须等于 workflow 输入 `finding_id`）
- `primary_run_id` / `primary_run_attempt`（必须等于输入；绑定原审查 run/attempt）
- `head_sha`（40 hex，必须等于当前 PR head，对标 argv[6]）
- `runner`（首批只 `pytest`）
- `nodeid`（该 head 上既有测试的 pytest nodeid）
- `patch.sha256`（小写 hex）、`patch.byte_len`、`patch.unified_diff`（UTF-8 统一 diff 正文）

gate 回填（作者提交则拒 `behavior_replay_author_forged_rerun`）：

- `gate_replay.baseline` / `gate_replay.red`：junit 摘要、exit_code、xml sha256
- `gate_replay.canonical_junit`：从绑定 run 下到的 quality junit 摘要

### JSON 样例（作者提交，无 gate_replay）

```json
{
  "kind": "behavior_replay",
  "result": "refuted",
  "pointer": "tests/test_lock.py::test_locked_behavior asserts the predicate at lock.py:12",
  "finding_id": "p1",
  "primary_run_id": "1234567890",
  "primary_run_attempt": "1",
  "head_sha": "0123456789abcdef0123456789abcdef01234567",
  "runner": "pytest",
  "nodeid": "tests/test_lock.py::test_locked_behavior",
  "patch": {
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "byte_len": 412,
    "unified_diff": "--- a/lock.py\n+++ b/lock.py\n@@ -10,7 +10,7 @@ def locked():\n-    return True\n+    return False\n"
  }
}
```

`patch.byte_len` 硬上限 **8192**。[推断] `workflow_dispatch` 输入走 `counterevidence_json` 字符串，不新开输入；超过上限本地/preflight 拒 `behavior_patch_too_large`。正文必须与 sha256 一致，否则拒 `behavior_patch_hash_mismatch`。

稀疏检出 `.github/workflows/gate-v2-disposition.yml:217-222` 今日只拉 `counterevidence_argv.py`；实现卡要把新模块加进同一清单，对标 `4607ae7` 给 argv 模块加的那一行。

## 2. 在哪里执行

### 现状（实测）

- `fc56376` 把 disposition 钉死在 `[self-hosted, linux, ci, git-source-service]`（workflow:172），`timeout-minutes: 10`（:173），`environment: gate-disposition`（:167-168），注入 `SILO_ACCESS_KEY` / `SILO_SECRET_KEY`（:175-176）。注释写明：Silo 在 tailnet，且只有 SOURCE-MODE=service 的宿主有镜像。
- `1f2046a` / 261023 设计：control 在 **bare 镜像**上只跑白名单 grep，加 `consume.lock`，不检出可写工作树、不跑测试。261023 非目标写明「不改反证命令白名单」。
- quality 作业（`.github/workflows/gate-v2.yml:534-541`）已在跑调用方测试：同仓合法人类 PR → `[self-hosted, linux, ci]`（**无** `git-source-service`）；fork / Bot / 非法作者 → `ubuntu-latest`。注释：「标签不证明镜像隔离」。quality 作业未见 Silo 注入；`primary`（:1355-1358）有。
- quality 把 junit 传到 `gate-quality-junit-${{ github.run_id }}-${{ github.run_attempt }}`（:904-911），并用 `xml.etree` 区分 failure / error / skipped（:1040-1105）。
- `primary` 对 draft / fork / hosted 整 job skip（:1344-1348）。fork PR 没有规范主审 audit。
- 调用方模板（`templates/caller-gate-disposition.yml`）注释：公开仓只映射仓级 Silo，不继承其他秘密。`workflow_call.secrets`（disposition.yml:26-32）只声明这一对 Silo 键。

### 三处对比

| | A. 现有 disposition control | B. 普通 `ci` 槽（无 git-source-service） | C. quality 作业预先产出 |
|---|---|---|---|
| 隔离 | 最差：与宿主 git 镜像同机 | 与 quality 同级；注释已声明标签≠隔离 | 与今日测试相同 |
| 凭据 | Silo + `gate-disposition` environment + `github.token` | 可不注入 Silo、不挂该 environment | quality 无 Silo（实测未见注入） |
| 耗时 | 并进现有 10 分钟预算；pytest 一枚 nodeid 通常秒～分钟，但和取审计/写回执抢同一 timeout | 单独 job，可给 10–15 分钟；多一次排队 | 审查时已付测试成本；disposition 零额外跑测 |
| 能否做红验 | 能跑，但不该跑 | 能：可写工作树 + 打 patch | **不能**：红验 patch 在申诉时才有。261023 已否决「主审时固化证据内容」 |

A 不可接受：在挂着 Silo、对着宿主镜像的 runner 上执行 PR 测试，等于把不可信代码放到取码源和对象存储钥匙旁边。`workflow_dispatch` 从 base 仓触发时秘密仍会注入，fork PR 尤其危险。

C 不能单独满足门槛 3（针对性红验）。C 的 junit 只用来绑定「这个 nodeid 在该 run/attempt 上收集并跑过、未 skip、基线绿」。

### 推荐：B，作为 disposition workflow 里一个无凭据 `replay` 作业

- `runs-on` 对齐 quality：同仓合法人类 → `[self-hosted, linux, ci]`（**禁止** `git-source-service`）；其余 → `ubuntu-latest`。
- **禁止** `environment: gate-disposition`，**禁止** `AWS_*` / Silo，`persist-credentials: false`。
- 只检出绑定 `head_sha` 的可写工作树（不要用 bare 镜像当 cwd 跑测试）。先无 patch 跑 nodeid（绿），再 `git apply` 后跑同一 nodeid（红）。junit 以 Actions artifact 回传；control 只校验、签发回执。
- 字面量反证：`replay` skip，control 维持今日 grep 路径。
- control 仍留在 git-source-service 上读审计、写 Silo，**不** exec 测试。

**fork PR**：`primary` 本就 skip，没有规范 audit，行为反证直接拒 `behavior_unreviewed_or_fork`（与「无规范主审就没有可绑的 run」一致）。即使有人手工 `workflow_dispatch`，`replay` 也拿不到 Silo。

**公开仓**：Silo 仍只进 control（模板已如此）。`replay` 无仓级秘密。公开仓的同仓人类 PR 若走 self-hosted `ci`，隔离级别与该仓 quality 相同，不额外放宽、也不额外收紧。

已否决：全量 replay 都上 `ubuntu-latest`（[推断] 与 quality 环境不一致时，环境故障会被误判成红验失败；应让环境故障走明确拒绝码，而不是换机碰运气）。已否决：在 quality 里预跑红验。

## 3. 重放与判定

### 首批运行器

只支持 **`runner: "pytest"`**。gate / agent-config 都是 Python；quality 已产 pytest junit（gate-v2.yml:1003-1006 的 legacy 路径，entry 路径约定 `$GATE_ARTIFACT_DIR/junit/`）。Go / Node / 其他 → `behavior_runner_unsupported`，调用方 ASK_HUMAN。

`--collect-only` **不能**单独当「已运行」：[推断] 它只证明可收集。门槛 2 要求实际收集并运行、未 skip。collect-only 只允许作辅助（nodeid 不在收集集 → `behavior_nodeid_not_collected`）。

### 机读证据（实测 + 推断）

复用 quality 已锁定的 junit 结构（gate-v2.yml:1040-1105，实测）：

- 子元素 `<failure>` → failure
- `<error>` → error
- `<skipped>` → skipped
- 皆无 → passed
- 目录无 xml → `junit_missing`；0 条 testcase → `junit_empty`；全 skip → `junit_all_skipped`

nodeid 对齐（实现卡用夹具锁死，下列映射为推断）：`tests/test_lock.py::test_locked_behavior` ↔ `classname="tests.test_lock"` + `name="test_locked_behavior"`；带参数的必须写全 `::test_foo[case]`，只允许恰好一条 testcase。

**绑定（规范 run 的 historical junit）**

从 `primary_run_id` / `primary_run_attempt` 取 artifact `gate-quality-junit-<run>-<attempt>`。该 xml 里目标 nodeid 必须是 passed（非 skip/error/failure）。缺 artifact → `behavior_canonical_junit_missing`。这把证据绑到审查 run，且不是粘贴日志。

**绿基线（replay 作业现跑）**

同一 `head_sha`、无 patch：pytest 对该 nodeid 的 junit 为 passed，进程 exit 0。否则 `behavior_baseline_not_green`。历史 junit 不能替代这一步：红验工作树是新检出的。

**红验（打上 patch 后再跑同一 nodeid）**

同时成立才算「断言失败变红」：

1. 恰好一条匹配的 testcase。
2. 有 `<failure>`，无 `<error>`，无 `<skipped>`。
3. `<failure @type>` 属于允许集：`AssertionError`、`Failed`（pytest.fail）。实现卡用最小夹具锁死 type 字符串，不靠本设计猜全。
4. `@type` 与 `message` 都不匹配超时/环境：`Timeout`、`TimeoutError`、`OSError`、`PermissionError`、`ConnectionError`。
5. pytest 退出码非 0（断言失败一般为 1）。作业/step 超时、被杀、无 xml → `behavior_replay_timeout` / `behavior_replay_no_junit`，**不算**红验成功。

对照：

| 现象 | 判定码 | 关 finding？ |
|---|---|---|
| 绿 passed，红 `<failure type="AssertionError">` | 接受 | 只关该 finding_id |
| 红 `<error>`（导入/fixture） | `behavior_red_is_error` | 否 |
| 红 `<skipped>` | `behavior_red_skipped` | 否 |
| 无匹配 testcase | `behavior_nodeid_not_collected` | 否 |
| step 超时 / 无 xml | `behavior_replay_timeout` | 否 |
| type/message 含 Timeout/OSError | `behavior_red_not_assertion` | 否 |

作者 JSON 里若带 `gate_replay` 或大段测试日志当 `output`：拒，对标今日伪造 grep output 仍要真实复跑（issue_receipt.py 测试 `test_false_positive_producer_rejects_forged_output_when_rerun_no_match`）。

## 4. patch 约束

finding 身份用 `canonical_finding_key`（convergence.py:466-492）：`file` / `line` / `category` / `severity`。邻居容差 `NEIGHBOR_LINE_TOLERANCE = 4`（:27）。

### 机器可判定（不合规即拒，不签发）

1. `unified_diff` 为 UTF-8 文本、无 NUL；`byte_len == len(utf-8)` ≤ 8192；sha256 匹配。
2. `git apply --check` 能干净打到绑定 `head_sha`。
3. 恰好改 **一个** 路径，且等于 finding.`file`（相对路径，无 `..`，无绝对路径）。
4. 不是新建/删除整文件（两侧都不是 `/dev/null`），无 rename、无 mode、无 binary。
5. 每个 hunk 的旧文件行区间与 `[line - 4, line + 4]` 有交集；每个被改行也落在该窗内。
6. 净变更行数 ≤ 12（`git apply` 后 `git diff --numstat` 的 added+deleted）。
7. apply 后 `git diff --name-only` 仍只有 `finding.file`。

打不进、或多文件、或行窗外 → `behavior_patch_*`，不 ASK_HUMAN（证据不合格，不是「需要人判语义」）。

### 交给 ASK_HUMAN（机器不可证「改的就是被指谓词」）

- finding.`file` 空 / `line` 非正整数（含 canonical 的 missing/null）。
- 谓词跨文件，或只在测试里、不在 finding.file。
- 行窗内改到的是相邻助手，不是被指断言——机器只能证「碰了这几行」，不能证「这就是那条谓词」。
- 测试 nodeid 绿且能被 patch 打红，但断言覆盖的是别的性质（覆盖缺口）。
- `runner != pytest`。
- `--class other` 或非 `none` 的 `--risk`（agent-config `gate_disposition.py:419-423` 已 ASK_HUMAN）。

范围：一笔行为回执只关输入的那一个 `finding_id`。同一测试碰巧打红的邻居 finding 不连带关闭（对标 issue_receipt 按 id/stable key 精确命中）。

## 5. 契约落点与拆卡

依赖：G1 → G2 → G3；A1 钉 G1 合入后的 gate SHA（不必等 v2 抬升也可先 pin 实现 SHA）；A2 跟 A1。G4 可并进 G3。agent-config 在 G3 合入前不要发行为 JSON，否则会被现网 `_counterevidence_reason` 当成缺 `command` 拒掉。

### G1 · 行为反证契约模块（gate）

- **目标**：平行于 argv v1 的纯句法契约；`kind` 判别；向量驱动。
- **落点**：`.github/actions/gate-aggregator/behavior_counterevidence.py`；`behavior_counterevidence_v1_vectors.json`；`tests/test_gate_convergence.py`（或新 `tests/test_behavior_counterevidence.py`）；sparse 清单暂可不改（无执行）。
- **验收**：`contract_version == 1`；合法 JSON `reason is None`；缺字段/错误 kind/超 8192/hash 不符有稳定 reason_code。现有 argv v1 向量全绿。字面量回执仍 schema 3。

### G2 · 无凭据 replay 作业（gate）

- **目标**：disposition workflow 增加 `replay` job；隔离与 quality 同级且无 Silo。
- **落点**：`.github/workflows/gate-v2-disposition.yml`；`tests/test_gate_v2_disposition_source.py` 的兄弟测试（断言 replay 的 `runs-on` 不含 `git-source-service`、job env 无 `AWS_`、fork/无主审拒 `behavior_unreviewed_or_fork`）。
- **验收**：literal_grep 时 replay skip，control 仍 grep；behavior 时 replay 先绿后红并上传 junit artifact；control `needs` replay 后再签。禁止 replay 引用 `secrets.SILO_*`。

### G3 · 签发与聚合消费 `kind`（gate）

- **目标**：`issue_receipt.py` / `convergence._counterevidence_reason` 按 kind 分支；junit 判定如第 3 节。
- **落点**：`issue_receipt.py`；`convergence.py`；`tests/test_gate_disposition_issue_receipt.py`；sparse 加入 `behavior_counterevidence.py`（对标 workflow:217-222）。
- **验收**：行为路径不调用 `rerun_counterevidence` 的 git grep；伪造 `gate_replay` 拒；AssertionError 红接受；`<error>` / skip / timeout 拒。字面量夹具（含 `40b9de9` absent）不变。

### G4 · 调用方模板说明（gate，可并 G3）

- **目标**：说明 `counterevidence_json` 现有两种 kind。
- **落点**：`templates/caller-gate-disposition.yml` 的 input description；可选 workflow:12 的 description。
- **验收**：仍只转发一个 JSON 字符串，不新 secret、不新 input 名。

### A1 · agent-config 生产者预检

- **目标**：`kind=behavior_replay` 本地校验后 dispatch；非法本地 Fail。
- **落点**：`scripts/git/gate_disposition.py`；`tests/git/test_gate_disposition.py`。加载 gate 的 `behavior_counterevidence.py` 须 pin **同一 SHA**（261017 对 argv 的要求）。
- **验收**：九段 grep 路径不变；合法行为 JSON 不再被 `COUNTEREVIDENCE_COMMAND_RE` 误杀；缺绑定字段本地 Fail，零 workflow 往返。

### A2 · review.md 路由

- **目标**：主脑知道何时用行为反证、何时仍 grep、何时 ASK_HUMAN。
- **落点**：`claude/skills/lead-workflow/references/review.md`（今日 :114-118 只写客观证伪 + grep）；`tests/skills/test_review_counterevidence_contract.py` 从「样例必须匹配 COMMAND_RE」扩成两种样例。
- **验收**：文档样例能过 A1 预检；覆盖缺口 / 非 pytest / 跨文件谓词仍写 ASK_HUMAN。

## 关键不变式

1. [实测] 字面量路径继续只认 argv v1 九段；行为路径不得改 `COUNTEREVIDENCE_ARGV_CONTRACT_VERSION`。
2. [设计约束] 调用方测试不得在 `git-source-service` + Silo 的 control 作业里执行。
3. [设计约束] 关闭范围 = 输入的那一个 finding id；绿基线必须现跑且 junit 无 skip。
4. [设计约束] 红验只认 junit `<failure>` 的断言类型，不认 `<error>` / 超时 / 环境异常。
5. [推断] quality artifact 名 `gate-quality-junit-<run>-<attempt>` 在绑定 run 上可下载；实现卡用「已知无该 artifact 的 run」作反向夹具。

## 已否决

- 把 pytest argv 塞进 `counterevidence_argv.py` 并升到 v2：污染字面量契约。
- 在 control / git-source-service 上跑测试：凭据与镜像暴露面不可接受。
- 主审时预产红验 patch：patch 在申诉时才有（261023 已否决同类项）。
- 只信历史 junit、replay 只跑红：不满足「受控执行、基线通过」。
- 用 collect-only 代替执行。
- 放宽行窗或允许多文件「最小 patch」：机器无法证谓词边界，应 ASK_HUMAN。

## 待验证前提

1. [推断] pytest junit 的 `@type` 在断言失败时稳定为 `AssertionError` / `Failed`。G3 夹具必须锁死，本设计不把它当已测事实。
2. [推断] Actions artifact 可按 `primary_run_id`+`attempt` 精确取到 quality junit；取不到与「xml 里没有该 nodeid」必须是两个拒绝码。
3. [推断] 单 nodeid 在 quality 同款 `ci` 槽上通常远小于 10 分钟；超限记 timeout，不降级到 control 上跑。
