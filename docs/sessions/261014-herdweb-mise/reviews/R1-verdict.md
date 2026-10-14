<!-- delegate-outcome: succeeded -->

## 裁定
failure-visibility: p2-only

审查端点固定为 `05389f40693c9a3909b6d6d2929e25c720aac853..9e72cb5d12ec516983d05b5f332befd5d8829edf`（PR #315 H0；远端 `headRefOid` 与 H0 一致）。发现 1 条 P2，阻断本轮将 PR 标 ready/合并，原因是 `install:false` 的真实行为不满足“只准备管理器、trust 归业务入口、平台 source checkout 不受 mise 环境影响”的契约；未发现 P1。个人档、owner 控制的配置不构成敌对攻击场景。

PR 当前为 draft；`test`、`actionlint` 两项均为 `SUCCESS`。这只证明测试与 YAML 静态检查通过，不证明第三方 action 或 herdweb consumer 已运行。按卡面基线说明，primary 不属于本仓该 PR 的预期检查。主干基线不可用；本轮无红色检查可分类，继承红状态仍记“未能判定”。

## Finding

### F1 — P2，阻断 ready/merge：第三方 action 的默认副作用超出契约，且发生在 gate 自有 source checkout 之前

- **违反条款**：设计文档 §不变式 3（`docs/sessions/261014-herdweb-mise/design.md:29-33`）与 §验收（`47-49`）；README 质量入口说明（`README.md:55-68,102`）；任务卡不变式 3 的平台隔离要求。
- **触发路径**：`quality-entry=entry` 且 `inputs.quality_prepare_mise=true` 时，生产 workflow 在 `.github/workflows/gate-v2.yml:579-583` 只传 `install: false`，随后才在 `:588-611` checkout gate 自有 action source 并运行 PR-size preflight。
- **真实上游源**：`jdx/mise-action` `v3` 解析到 commit `5228313ee0372e111a38da051671ca30fc5a96db`。`action.yml` blob `73da383185a775dbc37897ffac000e0a6675b5c9` 声明 `install:false` 只跳过 `mise install`，但 `cache`、`add_shims_to_path`、`env` 默认都为 true。`dist/index.js` blob `847bb47529896d4c6382aa1841214c23d62a25fc`：`81006-81030` 仍 restore cache、setup mise、执行 `mise --version`/`mise ls`/`mise env`；`81138-81143` 导出 `MISE_TRUSTED_CONFIG_PATHS=<cwd>`、`MISE_YES=1` 并添加 shims 到 PATH；`81146-81155` 将整个 mise 数据目录交给 cache restore；`81236` 添加 manager bin 到 PATH。执行体还依赖 sidecar `dist/sourcemap-register.js` blob `cb1fb13627f07ab9250566c7069e0756f882663a`。
- **独立执行证据**：直接运行真实上游 dist 分两阶段完成。首跑在隔离目录下载并执行了真实 mise manager，但 cwd 未切到 probe workspace，因此不以该次结果判断 trust/config。随后切到临时 caller workspace、去掉 workflow 未提供的 `working_directory` 输入，以 `install=false` 和 action.yml 其余默认值重跑，复用已下载的 manager，并用 `strace -f -e trace=execve` 记录实际子进程。校正后的 run 成功调用 `mise --version`、`mise ls`、`mise env --redacted --json`、`mise env --json`，没有调用 `mise install`；`GITHUB_PATH` 实际写入 manager `bin` 和 `shims`，`GITHUB_ENV` 写入 `MISE_TRUSTED_CONFIG_PATHS`、`MISE_YES=1`、配置中的 `GATE315_PROBE_ENV` 及 PATH。临时配置文件保持原字节。GitHub 文档说明 `GITHUB_PATH`/`GITHUB_ENV` 会影响后续 steps；mise 文档说明 `MISE_TRUSTED_CONFIG_PATHS` 信任目录内配置、`MISE_YES` 自动回答确认。故 action 不只是安装 manager：它预先改变后续 step 的 trust、环境和 PATH。
- **边界与不确定项**：本地 runner 没有 `ACTIONS_CACHE_URL`，action 尝试 restore 后报告 cache service 不可用并继续；没有观察到 cache hit。源码证明该 action 会缓存/恢复整个 `miseDir()`，而 mise 数据目录含已安装工具，因此已有 cache 时可能带回工具；这部分不能说已在真实 runner 命中。真实 gate source checkout 中具体命令是否被某个 shim 覆盖，取决于 caller 配置和 cache 内容，本次未现场观测；但 `GITHUB_PATH`/`GITHUB_ENV` 修改在 probe 中已实测，且当前步骤顺序确实把它们放在 gate source checkout 前。`mise env` 官方说明为只读且不会安装缺失工具；因此 finding 不声称 action 执行了项目安装。
- **严重度/阻断**：P2。主因是明确契约与上游默认行为不一致、平台步骤环境不再隔离；没有数据丢失、静默失败或崩溃证据，故非 P1。该问题阻断当前契约下的 ready/merge；若主脑决定接受 action 的 trust/env/cache 语义，应先同步 spec/README/测试，并把 action 移到 gate source checkout、PR-size preflight 和 source cleanup 之后。若“业务入口拥有 trust”必须保持，当前 action 参数没有关闭这些默认 trust/env 副作用的选项，需改 action 选择后再验。

## E2E 行为链

生产 YAML 的 boolean 默认 false、entry+opt-in guard、`install:false` 参数及步骤顺序均存在；GitHub 的默认 `success()` 语义使准备失败后续步骤跳过（[GitHub expression 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)）。上游 `dist` 执行行为与前述 probe 一致。新测试直接读取生产 YAML，但不运行 GitHub Actions expression/runtime，也不测试上游默认副作用。PR 仍是 draft，输入没有被真实 herdweb caller opt-in；真实 caller entry 的执行结果、runner cache 命中和实际工具路径覆盖仍未完成。最小后续消费证据是一轮有界真实 caller opt-in workflow：只采结构化 step conclusion、entry exit code 与必要的非敏感 PATH/工具名判据，不需要新增常驻 workflow 或监控。

## OCR 前置扫描与分诊

状态 `reviewed_fallback`（profile `deepseek` / model `deepseek-v4-flash`）；主腿 `minimax` 在 `900.030s` 超时，备腿成功（`188.120s`）。结构化 verifier：`completed`，1 条确认、0 条反驳、0 条未核，coverage `complete`；摘要 1203 字节。

OCR 标注一条 high：`jdx/mise-action@v3` 是本 workflow 唯一未固定 commit 的外部 action。源码事实复核为真（`.github/workflows/gate-v2.yml:523,649,891,1364` 的外部 action 均固定 SHA；`:581` 使用 `@v3`，测试 `:123` 锁定该 ref）。本仓分诊：P2 安全/可复现性观察，不作为阻断 finding。真实触发测量：PR 仍为 draft，输入默认 false，卡面记录的 herdweb consumer 尚未 opt-in，故当前没有真实 consumer 执行该引用；未来显式 opt-in 会触发。后果判定：没有观察到 tag 被移动或执行结果受损；按 personal 红线，不构成 P1。且本卡 spec 明确要求 `@v3`、本轮不改 action 引用策略，因此不以 OCR 的 high 标注推翻卡面契约，也不改引用。若 owner 以后要不可变 action 源，先变更 spec 与对应断言再另审。

## 验证与测试判据

- 定向测试：`UV_PROJECT_ENVIRONMENT=/tmp/gate315-review-261014-42/test-venv UV_CACHE_DIR=/tmp/gate315-review-261014-42/uv-cache uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_quality_mise.py tests/test_gate_v2_contract.py` → `229 passed in 79.08s`。
- pin 检查：`python3 scripts/check_pinned_uses.py` → `OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative`。
- 冻结 diff `git diff --check 05389f40693c9a3909b6d6d2929e25c720aac853..9e72cb5d12ec516983d05b5f332befd5d8829edf` → exit 0。
- base 红验：一次性 scratch worktree 在 base `05389f4` 仅拷入新增测试文件，运行默认关闭断言；pytest 以真实 `AssertionError` 失败，报 `workflow_call inputs must declare quality_prepare_mise`，`1 failed in 0.07s`，非导入/语法错误。临时树已自动注销。
- H0 变异红验：一次性 scratch 中将 workflow 默认值 `false` 改成 `true`；注入前断言替换计数为 1，现场确认行是 `quality_prepare_mise: { type: boolean, default: true }`。同一测试以 `assert True is False` 的 `AssertionError` 失败，`1 failed in 0.07s`；临时树已自动注销。

### 新增测试判据

- `tests/test_gate_quality_mise.py`：已实测缺失 input（base）与默认值 `false→true`（H0 scratch）都会 AssertionError；覆盖设计不变式 1。其 guard evaluator 只接受卡面要求的两段精确表达式，6 个 entry/legacy × true/false/未传组合会检查决策矩阵；它约束生产 YAML 字面契约，不是 GitHub runtime 模拟。测试同时锁 action 名称/参数、业务入口单次调用与步骤顺序、失败不吞及 legacy 独立性；没有测上游 action 默认 `cache/env/shims/trust` 副作用，这是 F1 的缺口。
- `tests/test_gate_v2_contract.py`：闭合集合断言纳入 `quality_prepare_mise`，避免 workflow_call 输入集漏项。
- reviewer 本轮没有新增或修改测试。

## 上游来源及 probe 留痕

- 官方源：[`action.yml`](https://github.com/jdx/mise-action/blob/5228313ee0372e111a38da051671ca30fc5a96db/action.yml)，blob `73da383185a775dbc37897ffac000e0a6675b5c9`；[`dist/index.js`](https://github.com/jdx/mise-action/blob/5228313ee0372e111a38da051671ca30fc5a96db/dist/index.js)，blob `847bb47529896d4c6382aa1841214c23d62a25fc`；`dist/sourcemap-register.js` blob `cb1fb13627f07ab9250566c7069e0756f882663a`。上游 `mise env` 行为见[官方 CLI 文档](https://mise.jdx.dev/cli/env.html)，信任环境变量见[官方 MISE 环境变量文档](https://mise.jdx.dev/configuration/environment-variables.html)和[settings 文档](https://mise.jdx.dev/configuration/settings.html)；后续 step 环境继承见[GitHub workflow commands](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions)。
- 临时 probe 文件仅在 `/tmp/gate315-review-261014-42/action-probe/`：上游 JS/sidecar、下载的 mise binary、mise 临时解包文件、probe workspace 的 `mise.toml`、GITHUB_PATH/ENV/OUTPUT/STATE 文件及 execve trace。没有写入审查 worktree；cache 未命中，实际 GHA consumer 未执行。原始 action 日志未放入 verdict。

## 陷阱、闸、绕过、偏差

- 闸：default false + `entry && opt-in` 守卫挡住未传/false/legacy；新增测试对 production YAML 有约束。未发现绕过此 guard 的代码路径。
- 陷阱：Contents API 对 4.9MB `dist/index.js` 返回 `encoding=none`/空 content，改取官方 raw 文件并以 blob SHA 校验；直接执行 bundle 首次因缺少 `sourcemap-register.js` 失败，获取真实 sidecar 后才运行。第一次 probe 的 cwd 指向审查树，发现后改到隔离 caller workspace 用精确 action inputs 重跑；没有改动仓库文件。
- 偏差：未在真实 GitHub consumer runner 执行；这是卡面已声明未验收的消费边界，不以本地 probe 或 draft CI 代替。pickup 简报无交接单；仓级 memory index 不存在，共享记忆搜索无命中。读取了 H0 diff 内受审的 `progress/mise-progress.md` 作为变更文档，但没有把其中执行器自述当作验证证据；派发 report/会话推理未读取。
- 最贵的一步：真实上游 action probe 下载并运行约 162MB mise binary；定向测试耗时 79.08 秒。新增测试判据：见上节；无测试新增。
