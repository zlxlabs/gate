# 独立审查 verdict：gate#278 disposition 取码修复

- 审查范围：`f8c16beaeddfe8acc811a6664658e5d457da47dc..b951586217bc26705b07be77d2d7c3f1ef13209a`（固定 H0，不随分支移动）。
- 风险档：`personal`；审查依据：任务卡 spec 与代码。未读取实现报告或实现方推理。
- 结论：通过；无本次 diff finding。
- `failure-visibility: clean`

## Spec 核对与结论

1. **service/origin checkout 位置**：workflow 在 `.github/workflows/gate-v2-disposition.yml:166-183` 给 `GATE_CHECKOUT_PATH` 声明 `_gate-disposition-src`。`tests/test_gate_source.py` 的 service 与 origin 测试分别执行该 workflow checkout step 的实际 `run` 和静态 env 值，并核实稀疏文件落在该目录、workspace 根不被写入。符合两种主机声明下都物化到 workspace 子目录的要求。
2. **四个消费者**：`SILO_STORE`、`SILO_EXEC` 在 workflow `:160-164` 指向子目录；convergence 加载在 `:276`，receipt producer 在 `:328` 指向同一目录。`tests/test_gate_v2_contract.py` 检查实际 workflow env/run 中的路径和 checkout 字节；convergence 测试在仅有子目录副本的 workspace 运行 inline Python；receipt 测试运行实际 issue step 并核对生成回执。消费者都从同一目录取得工具。
3. **source service 失败不回源**：本次没有改 source helper 的分支。现有 `tests/test_gate_source.py:689-705` 将 client 失败断言为 fatal，并确认网络 argv 为空；本次 service checkout 测试也确认无 origin 网络调用。
4. **证据/身份接受域**：本次只调整工具路径。workflow 仍按相同参数构造 scope 并调用 issue producer；现有 `tests/test_gate_v2_contract.py:325-344` 锁定 `github.actor`、`github.actor_id`、receipt argv 和禁用 `triggering_actor`。未发现接受域扩大或缩小。
5. **其他 workflow**：代码修改仅在 disposition workflow 与相关测试；新增 workflow 扫描要求各 workflow-sha checkout 声明非空 workspace 子目录。固定范围还包含 `docs/sessions/261003-priority-fixes/progress/card-A-progress.md`，按输入隔离要求未读其内容。代码范围未发现其他 workflow 行为变化。
6. **新增测试 helper/抽象**：`_disposition_checkout` 服务于 service/origin 两个行为用例；`_checkout_shaped_workspace` 被三个消费者行为测试复用。`_disposition_step`/`_disposition_env` 是提取真实 YAML producer 的薄适配器，避免重造 env/run；未新增生产抽象、状态或配置层。

## P1 两问与降层三问

- **P1 问一，真实路径会触发吗？** 旧 workflow 的 service 分支把空 `GATE_CHECKOUT_PATH` 交给 source helper，会触发 `SOURCE-PATH-MISSING`；origin 分支在 `scripts/gate_bounded_retry.py:214-215` 把空路径解析为 workspace 根目录，违反子目录不变式。两种主机行为用例都运行修复后的真实 workflow step；base 红验确认 service 分支按 `SOURCE-PATH-MISSING` 失败。
- **P1 问二，触发后果能否接受？** service 分支是 receipt 前可见失败；origin 分支沿 workspace 根目录继续，旧路径与旧消费者相符，但位置违反 spec，且可能与 workspace 中的调用仓内容冲突。固定 diff 将四个消费者一并迁至子目录，没有证据表明这导致错误 receipt 或扩大写入权限；因此没有本次 P1 finding。
- **成功写 receipt 前的不可逆动作**：此前为 checkout、MagicDNS/hosts 处理、GitHub 查询与 Silo `get`/缺失时下载，以及临时目录写入；没有 Silo `put-dir` 等不可逆发布写入。receipt 由 `:304-340` 写入临时输出目录，随后才在上传步骤 `:345-355` 调用 Silo 写入。
- **真实 job 的身份唯一性**：job 使用 `github.actor` 与 `github.actor_id` 作为 approver 名称和数值 ID；契约测试确认二者来自该 job 的 GitHub 上下文，未换成 `triggering_actor` 或输入参数。生产 run 未执行，因此未观测实际求值后的值；代码未改变该身份域。
- **保护覆盖写入还是行为**：`control` job 的 GitHub Environment `gate-disposition` 保护整个 job；它覆盖 receipt 与后续上传步骤的执行，不只包住最后的写操作。helper 的证据和身份校验仍负责行为约束。本次路径修复未绕过或改写任一保护。
- 三问均未发现与本卡 spec 冲突。

## Finding

无 finding；无 P1/P2/P3 项。

## 验证与 OCR

- `git diff --check f8c16beaeddfe8acc811a6664658e5d457da47dc..b951586217bc26705b07be77d2d7c3f1ef13209a`：通过。
- 指定测试：`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_source.py tests/test_gate_v2_contract.py`：`296 passed in 105.29s`。
- base 红验：从 H0 拷入两份测试文件，在 `f8c16be` 隔离树运行两条新增测试；机器断言 `tests=2 failures=2 errors=0`。service 用例以 `SOURCE-PATH-MISSING: GATE_CHECKOUT_PATH` 失败；convergence 用例因旧路径在 workspace 根不存在而 `FileNotFoundError`。两条失败都对应本次修复。
- OCR：`status=reviewed`，profile `minimax`，`findings=[]`，`coverage=complete`；不是 `skipped`。无 OCR finding。无第二模型子进程。
- 派发时基线 API 不可用（`gh api request failed`），因此继承红无法判定；本次定点测试无新红，红验中的两条是预期回归红。
- 真实生产发布不在范围，未验证真实 service/origin runner、GitHub 运行时环境值、MagicDNS、Silo 读取或上传写入。

## Git 现场（审查时）

- `git status --short --branch`（新增 verdict 前）：工作树干净；分支 `card/disposition-review-261004`，HEAD 为固定 H0 `b951586217bc26705b07be77d2d7c3f1ef13209a`。该本地分支无 upstream；提交后按同名远端分支显式推送。
- `git log --oneline --decorate -5`：`b951586` fix checkout subdirectory；`8c3cea3` add checkout/consumer contracts；基线 `f8c16be`。
- `git show --stat --oneline b951586217bc26705b07be77d2d7c3f1ef13209a`：H0 修改 disposition workflow 与 progress 文件；本审查没有读取 progress 文件内容。

## 过程记录

- **踩坑**：接手简报脚本是 shell 文件，首次误用 Python 调用后失败；改用 `bash` 后成功。红验首次使用了错误的 JUnit 根节点字段，随后改为对各 `testsuite` 汇总，最终机器断言通过。
- **闸与绕过**：未发现绕过。service 失败不回源、环境保护、证据/身份校验均保持；没有调用生产写入端点。
- **偏差**：任务卡的派发时主干基线不可用，继承红只能标记“无法判定”。未改实现或测试；只新增本 verdict。
- **最贵一步**：OCR 主腿约 112.8 秒；两文件测试 105.29 秒。
