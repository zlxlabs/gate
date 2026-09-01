# gate-A disposition workflow_call 改造：R2 verdict（换家复验）

## 审查边界与本轮新证据

- 冻结范围：`e0bd30f88e99decb1e35a6601768d41615defbf8..16368884bd7fe9a9edc3711c904afbfbddcee00a`。本工作树 HEAD 即 H1=`1636888`，审查期间新提交不进本轮。
- 本仓 `risk-tier: personal`。P1 红线 = 数据丢失 / 静默出错 / 崩溃，另含「agent 给自己开绿灯」。安全类低于红线一律 ≤P2。
- 本轮新证据（均在读完 r1 verdict 与 H0 全文 diff 之后取得，不是同一份 diff 换措辞）：
  1. H0..H1 修复增量 `1708fef..1636888`（F1：checkout 前 40-hex 闸 + 契约测试 + 进度段）。
  2. 对抗探针：用与 workflow 相同的 bash `[[ "$GATE_REF" =~ ^[0-9a-f]{40}$ ]]` 对大写 hex / 空白 / 换行 YAML 片段 / 分支名实测；YAML 解析确认两触发器共用同一 job 的同一 step。
  3. 红验：在 base `e0bd30f` 临时 worktree 仅注入 H1 的 `tests/test_gate_v2_contract.py` 跑 2 条新增测试，均以 `AssertionError` 转红（原文见下）。
  4. OCR `status=reviewed` / `coverage=complete`（4 条工具意见，落地前重判）。
  5. H1 上只读跑 6 条 disposition 契约测试：`6 passed in 0.40s`。
- r1（codex，`1ef2722` / `review-gate-a-r1-verdict.md`）判 fail，F1 已修；本轮不重复计 F1。r1「已核对不成立」换措辞重提不算新增。

## H0..H1 增量四问（`1708fef..1636888`）

范围：`.github/workflows/gate-v2-disposition.yml` +10、`tests/test_gate_v2_contract.py` +19、进度存档 +14。无新文件。

1. **是否只修登记在案的 F1？** 是。唯一生产改动是 checkout 前新增 step `Require 40-hex gate_ref`（`.github/workflows/gate-v2-disposition.yml:38-46`），读 `inputs.gate_ref`，正则 `^[0-9a-f]{40}$` 失败则 `::error` + `exit 1`。测试 `test_disposition_requires_lowercase_40_hex_gate_ref_before_checkout`（`tests/test_gate_v2_contract.py:254-270`）锁 step 名、顺序、env、正则、`exit 1`、无 `rev-parse` / 无 `git `。进度段只记录该修复，禁止项未改。
2. **是否新增未经批准的抽象？** 否。无新接口 / 包装层 / 配置项 / 模板。
3. **状态 / 事实源 / fallback 是否无依据增加？** 否。闸不解析 branch/tag、不做 SHA 规范化；测试负断言 `rev-parse` 与 `git ` 不在该 step 的 `run` 里。checkout 仍钉 `ref: ${{ inputs.gate_ref }}`（`:52`），没有改写后再消费的第二事实源。
4. **是否留下双路径？** 否。`jobs` 只有 `control`；`workflow_dispatch` 与 `workflow_call` 输入集合相同（见下全量复验），共用 step[0] 闸 → step[1] checkout。无第二条校验/解析路径。

增量四问通过，不构成新增 P1。

## 本轮 finding

无新增 P1 / P2 / P3。r1 F1 已在 H1 闭合，不重复登记。

## 全量复验（`e0bd30f..1636888`）+ 对抗视角

对照 spec 1–6 与追加闸条款：

| spec | 结论 | 证据 |
|---|---|---|
| 1 两触发器 + 六输入 + checkout 钉 `zlxlabs/gate` @ `inputs.gate_ref` | 通过 | `gate-v2-disposition.yml:3-19` 两触发器输入集合相等；`:51-52` |
| 2 caller 模板 pin + 透传 + 恰好三权限 | 通过 | `templates/caller-gate-disposition.yml:16-18,26,33`；`tests/test_gate_v2_contract.py:1306-1338`。`workflow_dispatch` 不暴露 `gate_ref` 是刻意：`with.gate_ref` 与 `uses` 同一占位符，避免人手改 SHA。OCR 对此判 high 已复核为不成立（与 r1 同结论）。caller 三权限是锁定设计，不回退。 |
| 3 `sys.modules` 先于 `exec_module` + spec/loader 判空 | 通过 | `gate-v2-disposition.yml:122-126`；测试 `:183-191`；红验见下 |
| 4 `sparse-checkout-cone-mode: false` | 通过 | workflow `:56`；测试 `:226-240` |
| 5 environment + 安全负断言 + 高风险路径零改动 + callee 权限 | 通过 | job `environment.name: gate-disposition`（`:33-34`）；`permissions` 三项（`:22-25`）；测试 `:157-161` 仍锁无 `evidence` / 无 `pull-requests: write` / 无 `checks: write` / 无 `statuses: write`；`git diff --name-only e0bd30f..1636888 -- .github/actions .github/workflows/gate-v2.yml scripts/` 为空 |
| 6 改坏即红（AssertionError） | 通过 | 本轮红验两条均为 `AssertionError`；H1 上同 6 条绿 |
| 追加：checkout 前 40 位小写 hex、两路径同闸、无解析无 fallback | 通过 | 见增量四问与对抗探针 |

对抗绕过面（本轮新跑）：

- 大写 / 混写 40-hex、39/41 位、`main`、tag、空串、前后空白、换行、YAML 伪键、`$(echo pwned)` 前缀：全部 REJECT。仅 40 位小写 hex ACCEPT。
- `GATE_REF` 经 `env:` 注入，GHA 先解析 workflow 再求值表达式，输入里的换行不能长出新 YAML 键；bash `"$GATE_REF"` 不二次展开引号。
- 两触发器没有各自的闸：同一 `control` job、step[0] 无 `if`、无 `continue-on-error`；非法输入在 checkout 前 `exit 1`，后续 step 不跑。
- checkout 消费的是 `inputs.gate_ref` 原值，不是闸的输出；即使将来有人加 `git rev-parse` 中间量，现有 `ref == ${{ inputs.gate_ref }}` 契约仍会红。
- caller 组合：模板把 `gate_ref` 写死为 pin 占位符，业务仓 `workflow_dispatch` 不能另传 SHA；callee 侧闸仍挡住直接 dispatch 的 branch/tag（F1 闭合点）。
- `continue-on-error` / `if:` 当前均不在闸 step 上。缺一条负断言不构成**当前**绕过；形态空间无界，不单开 finding（否则会陷入准入特判循环）。

## 已核对且不成立的意见

- r1 F1（gate_ref 未强制 40-hex）：H1 已修，不重复计。
- OCR high：caller `workflow_dispatch` 未暴露 `gate_ref` 会导致手动触发空输入失败。不成立。caller `:33` 把 `gate_ref` 固定为 `__PINNED_GATE_SHA__`，测试 `:1330` 锁 `with.gate_ref == uses` 的 pin。r1 已按 README 模板契约驳回，本轮换证据源后结论不变。
- OCR medium：`run.index("<<'PY'")` 在 heredoc 变形时抛 `ValueError`。不成立为缺陷。r1 已核：`:188-191` 先断言目标串存在再比顺序；helper 的 `index` 是存量测试风格，且无法溯源到本卡 spec。换措辞不记新增。
- OCR medium（unverified）：subprocess 继承测试进程环境。不记。无法溯源 spec；personal 档下本地 pytest 加载本仓 `convergence.py`，不构成生产静默出错或门禁绕过。
- OCR medium（unverified）：checkout 未设 `persist-credentials: false`。不记。无法溯源 spec；sparse 只取 `zlxlabs/gate` 两个已审文件，后续 step 本就持有 `github.token`。低于 personal 红线，且不是「agent 开绿灯」路径。
- 质疑 caller `actions: write`：锁定决策 2，机理（caller∩callee 交集）无反证，意见不成立。

## 工具标注 / 本仓判定 / 两问答案

| 工具标注 | 本仓判定 | 两问答案与依据 |
|---|---|---|
| OCR high：caller dispatch 缺 `gate_ref`（verifier=refuted） | 不成立，不记 finding | ①不会按该理由触发：模板硬编码 pin，不会把必填 `gate_ref` 留空。②无后果。 |
| OCR medium：heredoc `index` → ValueError（verifier=confirmed） | 不记 finding（r1 已驳） | ①真实使用下目标串仍在则不触发。②即使触发也是测试崩溃而非生产静默/绕过；无法溯源 spec。 |
| OCR medium：subprocess 未裁环境（unverified） | 不记 | ①只在 pytest 触发。②加载受控脚本，不接受不可信输入；后果可接受。 |
| OCR medium：未钉 `persist-credentials: false`（unverified） | 不记 | ①需历史 SHA 上那两个文件作恶。②job token 本就给后续 step；不构成门禁自放行。 |
| 对抗探针：大写 hex / 空白 / YAML 换行 | 闸行为符合 spec，无 finding | ①这些输入在真实 dispatch 会出现。②当前全部 REJECT，后果是 fail-loud，可接受。 |

OCR envelope：`status=reviewed`，`profile=minimax`，`coverage=complete`，`verify_status=partial`（两条超时未复核）。未把 `skipped` 说成扫过。

## 红验抽查证据

现场：`git worktree add --detach /tmp/gate-r2-red-e0bd30f e0bd30f88e99decb1e35a6601768d41615defbf8`，仅把 H1 的 `tests/test_gate_v2_contract.py` 拷入。注入确认：`INJECTION_OK`；`git diff --stat` 为 `tests/test_gate_v2_contract.py | 163 ++++++++-`（160 insertions, 3 deletions）；base workflow `grep` 无 `Require 40-hex` 与 `sys.modules[spec.name]`（`BASE_WORKFLOW_HAS_NEITHER`）。跑完后 `git worktree remove --force`。

抽查 1：`test_disposition_requires_lowercase_40_hex_gate_ref_before_checkout`（H1 新增，锁 F1）

```
F                                                                        [100%]
=================================== FAILURES ===================================
_____ test_disposition_requires_lowercase_40_hex_gate_ref_before_checkout ______
tests/test_gate_v2_contract.py:260: in test_disposition_requires_lowercase_40_hex_gate_ref_before_checkout
    assert validate_name in names
E   AssertionError: assert 'Require 40-hex gate_ref' in ['Checkout disposition producer', 'Resolve current PR head and canonical primary audit', 'Construct canonical scope and derive epoch', 'Issue immutable disposition artifact', 'Upload immutable disposition artifact']
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_disposition_requires_lowercase_40_hex_gate_ref_before_checkout
1 failed in 0.16s
```

抽查 2：`test_disposition_inline_python_registers_sys_modules_before_dataclass_exec`（锁 spec 3）

```
F                                                                        [100%]
=================================== FAILURES ===================================
__ test_disposition_inline_python_registers_sys_modules_before_dataclass_exec __
tests/test_gate_v2_contract.py:188: in test_disposition_inline_python_registers_sys_modules_before_dataclass_exec
    assert register in source
E   assert 'sys.modules[spec.name] = module' in 'import importlib.util\nimport json\nimport sys\nfrom pathlib import Path\n\naudit = json.loads(Path(sys.argv[1]).read...nt(json.dumps({"scope": scope.as_dict(), "epoch": module.derive_epoch(scope)}, sort_keys=True, separators=(",", ":")))'
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_disposition_inline_python_registers_sys_modules_before_dataclass_exec
1 failed in 0.05s
```

两条红类型均为 `AssertionError`，不是 ImportError / AttributeError / SyntaxError。H1 上同组 6 条契约测试 `...... 6 passed in 0.40s`，故非恒真测试。

对抗探针原文（与 workflow 同一谓词）：

```
ACCEPT	lowercase-40	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REJECT	uppercase-40	AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
REJECT	mixed-case-40	Abcdef0123456789abcdef0123456789abcdef01
REJECT	39-chars	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REJECT	41-chars	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REJECT	branch-main	main
REJECT	tag	v1.2.3
REJECT	empty	''
REJECT	leading-space	\ aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REJECT	trailing-space	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\
REJECT	leading-newline	abc$'\n'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REJECT	cmd-subst-prefix	\$\(echo\ pwned\)aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REJECT	newline-then-true	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa$'\n'true
REJECT	yaml-newline-key	deadbeefdeadbeefdeadbeefdeadbeefdeadbeef$'\n'\ \ FOO:\ bar
REJECT	embedded-dquote-39plus	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"
```

## 熵增审查

| 新增物 | 是否熵 +1 | 第二消费者 / 单消费者理由 |
|---|---|---|
| `workflow_call` + `gate_ref` 输入 | 否 | spec 1 要求的跨仓发布边界；caller 模板与直接 dispatch 都消费 |
| `templates/caller-gate-disposition.yml` | 否 | spec 2 要求的业务仓接入模板；下游仓是第二消费者，不是无消费者转发层 |
| step `Require 40-hex gate_ref` | 否 | r1 F1 的最小 fail-fast，不是新抽象 |
| 契约测试增量 | 否 | 改坏即红的锁，不是运行时状态 |
| `docs/sessions/.../progress/gate-a-progress.md` | 否 | 会话进度，无运行时消费者 |

无单实现接口、无仅转发包装、无镜像状态。未为过测试加 fallback。

## 结论

H0..H1 增量四问通过；全量复验无新增 P1。r1 F1 已修且有 AssertionError 型红锁。审查执行完成。

verdict: pass
