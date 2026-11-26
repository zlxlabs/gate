# DESIGN-note：按领域拆分 gate 聚合器与收敛模块（gate#453）

## 目标

按四张可独立发布的生产重构卡，将 `.github/actions/gate-aggregator/convergence.py` 与 `aggregate.py` 按领域边界拆开，消除收敛模块的文件体积例外，为后续纯判定、历史/I/O、渲染/发布改动提供稳定接缝。整批生产重构是纯结构迁移：既有输入、输出、回执 schema、裁决次序、失败原因与可观测行为不变；本 DESIGN-note 与卡 1 同批入库。

## 非目标

- 不改变任何收敛、处置或聚合裁决语义，不改回执 schema，不混入 gate#451 投影修复或其他功能。
- 卡 1 不动 `aggregate.py`，不拆 `tests/test_gate_aggregator.py`，不改 `tests/test_gate_v2_contract.py` 的断言与行数。
- 不以抬高 `.file-size-exceptions.json` ceiling、压行、删注释/断言或引入通用 `*_helpers` 层解决体积问题。
- 不引入非标准库运行依赖、fallback、重试或防御式异常吞噬；不改 `gate-v2.yml`（其 `_gate-silo-src` 已包含整个聚合器目录）。

## 为什么不是分区 / 删除 / 约定

- **分区**：现有 2399 行 `convergence.py` 把不可变数据合同、回执证据句法、处置裁决和状态机重放混在一个模块中。按职责分区为合同 → 回执句法 → 处置消费/裁决 → 收敛状态机，采用单向导入 DAG；不另建运行时进程或 I/O 边界。
- **删除**：没有可安全删除的收敛或处置能力；本批只移动定义和对应依赖，不删功能。`convergence.py` 的两个产物渲染入口保留原位，以维持 `tests/test_gate_v2_contract.py` 对源码位置的锁定。
- **约定**：仅靠“不要继续往大文件加代码”无法约束职责耦合与稀疏检出文件闭集。由模块边界、明确行数上限、单一 dataclass 身份测试、真实 workflow sparse 清单物化测试和运行时测试共同锁定，不把约定当作唯一防线。

## 方案要点与已否决方案

**四卡生产拆分与依赖 DAG**（后卡依赖前卡已发布的兼容入口；卡 2–4 不回头重写卡 1 的合同）：

1. **卡 1：收敛/处置领域拆分**（本卡）。模块 DAG 为 `convergence_contract.py` → `disposition_receipts.py` → `dispositions.py` → `convergence.py`。合同模块承载共享常量、异常、不可变 dataclass 与纯规范化；回执模块承载解析和证据句法；处置模块承载邻域匹配、处置裁决与消费；`convergence.py` 保留状态机/回执重放并显式重导出旧接口。加载器使用固定 `sys.modules` 名称，缓存先查、模块先登记再执行，确保 dataclass 身份唯一。`evaluate_round()` 保持经 `convergence.record_dispositions` 全局名调用。
2. **卡 2：`aggregate.py` 纯判定拆分**。先抽离 `evaluate()` 周边的身份校验、终态/收敛 envelope 构造、合成 audit 与纯决策函数到独立纯计算模块；`aggregate.py` 保留显式兼容导出，并让原有 monkeypatch 接缝仍位于调用模块。卡 2 依赖卡 1 的收敛兼容入口，不改 GitHub/Silo I/O。
3. **卡 3：`aggregate.py` I/O 与历史/上轮拆分**。把 GitHub REST、Silo 读写、terminal/disposition 历史与上一轮/previous-context/carry 数据获取投影按领域移出；依赖卡 2 定下的纯决策接口。调用方仍由 `aggregate.py` 控制，外部 I/O 顺序与失败语义保持原样。
4. **卡 4：渲染与面板发布拆分**。将 finding summary、状态面板/汇总 Markdown 渲染及评论发布接缝按纯渲染与发布副作用分开；依赖卡 3 的历史与 I/O 边界，`aggregate.py` 继续作为 CLI/兼容入口。

**测试文件策略**：`tests/test_gate_aggregator.py`（6065 行）和 `tests/test_gate_v2_contract.py`（4067 行）不随生产卡机械切割，保持断言原样；待四卡生产边界稳定后，单独开后续测试拆分卡，按领域迁移测试并保留全部断言。每张生产卡只增加属于该模块契约的聚焦测试。

**已否决方案**：

- 一次性拆完 `aggregate.py`、`convergence.py` 和超大测试文件：diff 过大、发布回滚面过宽，且无法逐卡证明行为等价。
- 将所有代码塞进一个共享 helper/support 模块、跨层调用私有实现或使用动态 `__getattr__` 代理：隐藏依赖、破坏 monkeypatch 与符号可见性，并不形成领域边界。
- 让每个新模块重新加载 dataclass 定义：会造成类身份分裂，使 `isinstance` 失效；固定 canonical `sys.modules` 键是本方案的必要加载约定。
- 循环导入或由处置模块回调状态机：形成隐式反向依赖；职责只允许沿 DAG 向下取用。
- 通过提升文件 ceiling、删注释/断言或压行过关：只延续根因，不接受。
- 借重构混入新裁决、schema、fallback 或重试：会使 base/head 行为等价无法成立。

## 关键不变式与验收路径

1. **模块图与体积**：卡 1 仅有 `convergence_contract.py` → `disposition_receipts.py` → `dispositions.py` → `convergence.py` 的单向依赖；三个新模块均 ≤800 行（合同目标 ≤600），`convergence.py` ≤1200 行并远低于 1500 行硬限；其 `.file-size-exceptions.json` 例外删除，其余三条 ceiling 原样不动。
2. **接口与身份**：旧 `convergence.py` 的公开及被消费内部符号继续显式可导入；不得用动态属性代理。所有入口所见的 `Scope`、`DispositionReceipt` 等 dataclass 是同一类对象。`evaluate_round()` 的回执记录通过 `convergence` 模块全局名调用，以保持 monkeypatch 兼容。
3. **行为等价**：回执解析、稳定 finding key、邻域消费、状态机、幂等/冲突处理及失败 reason 原样保持。既有 `tests/test_gate_v2_contract.py`、处置回执、重放和聚合器测试零断言修改通过。
4. **稀疏发布闭集**：`gate-v2-disposition.yml` 的 `GATE_CHECKOUT_SPARSE` 包含三个新模块；`test_no_runtime_install.py` 登记入口；`test_gate_source.py` 显式复制清单仅同行追加三项，文件净增 0 行。新增契约测试从真实 workflow YAML 解析 sparse 清单，将列出的文件复制到隔离目录后以 importlib 加载 `issue_receipt.py` / `convergence.py` 并构造回执，证明跨文件发布边界闭集完整。
5. **验证与发布**：先跑卡面窄验证和 `python3 scripts/check_pinned_uses.py`；CI 验证模式下提交并 push 后触发远程 CI，不在执行器进程等待远程终态。PR 正文只写 `Refs #453`。主脑审查后以 merge commit 合入；合入后由 canary 绿的 `v2-tag-sync.yml` 自动提升移动标签，无人工 caller pin bump。关单前必须从远端确认 `refs/tags/v2` 包含修复 SHA。
