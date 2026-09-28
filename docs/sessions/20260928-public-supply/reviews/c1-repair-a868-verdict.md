# C1 修复后独立审查结论

审查对象固定为 `85916ed5c4f9d181fec866f15d1bb5f58111c9c8..a8685d49b5016a504e327030b89a9e8931d45114`。先专项审查了 `89bf4abc6dd0ef2dceab931822bdebab2c36a899..8e894d5d2e166d76f94ceb3ebdff6d88c3bd4156`，再复验完整固定范围；没有跟随移动分支。

本轮已穷举这些不变式轴：可信仓库 ID 的凭据源选择及四个 job 的 AWS 环境设置；managed 与 legacy 的凭据读取、缺省、显式选择和 fail-fast；aggregate 的子进程 argv/env、源码路径缺省值及 standalone 兼容；15 个 Silo 数据操作的 wrapper 与 caller repository ID 命名空间；quality 的 Silo 凭据隔离；profile 缺失时不回退，以及 managed 空目录 skip 的校验；disposition 的 legacy 路径和可选 secret schema；实际子进程 producer 与 HTTP/S3 payload 断言；fork、draft、hosted skip 条件及 terminal、ledger、review policy；新增抽象、状态、文件、配置和日志字符串的熵增与可检索性。无 SQL 改动，因此数据库回读及 SQL 占位符轴不适用。

专项修复只在 aggregate 子进程环境中为缺省 selector 设置 `managed-profile`，测试同时覆盖 selector 缺省、显式 managed 和显式 legacy。`setdefault` 保留显式 legacy；wrapper 仍对空或未知 selector 失败。没有发现该增量引入新的抽象、fallback 或双路径。完整范围中，managed 模式清除 AWS 环境凭据，store 只从固定 profile 读取；已有测试覆盖五类 store 命令、profile 缺失时失败、真实 wrapper 子进程 argv/env，以及本地 HTTP/S3 请求和对象字节。

我没有运行测试；只读核对了指定的 standalone 红绿、focused H2 和 full H2 日志：红验命中缺省 selector 回归，绿验 10 项通过，focused 598 项通过，full 1273 项通过。OCR 前置结果为 `reviewed_fallback`，后备腿成功并报告了原 aggregate 缺省兼容问题；该问题已由本次增量修复，这不是对固定 head 的再次 OCR 扫描。

## P2：设计文档未明确 200 行预算对应的冻结范围

**位置：** [design.md](docs/sessions/20260928-public-supply/design.md:10)；相关卡片预算见 [design.md](docs/sessions/20260928-public-supply/design.md:25)。

顶部写明 runtime C1 预算为 200 行，最小候选表另给兼容修订卡 160 行预算，但没有为 200 行预算标注对应卡片或冻结范围。按各卡范围核对，初始 runtime 区间新增 189 行，兼容修订区间新增 151 行，分别在 200 和 160 以内；这不是源码超出单卡预算。问题在于文档没有说明 200 行预算与后续兼容卡预算如何适用于同一 PR，读者可能据此得出不同的验收结论。

**失败场景：** 后续审查按整个 PR 累计，或把 200 行解释为某张卡的预算时，会对同一产物采用不同的通过标准。

**有界修复任务：** 仅澄清设计文档中的冻结范围和预算口径，标明 200 与 160 分别对应的卡片及范围，并说明 PR 累计统计如何适用；保持两个预算数值不变，不以文档改写追溯放宽预算。验证时对对应固定范围分别核对 `git diff --numstat`，并确认文档明确映射且预算数字未变。

该文档口径问题不触发运行时数据丢失、静默错误或崩溃的 personal P1 红线。没有发现其他源码 finding。

## 剩余验证盲区

生产 profile 尚未部署；profile 的实际权限、可信 runner 可读性及 quality runner 不可见性未在生产环境验证。all28 隔离、真实 adapter/模型工具路径、Silo 服务端 ACL，以及 public、private、external fork、Dependabot、draft→ready 矩阵仍未验收。本次不将这些部署前置未知表述为源码已通过，也不据此新增源码 finding。

failure-visibility: p2-only

**结论：需修复后复审。**

原verdict不能改clean；澄清后的docs-onlydiff/root将定点验收消歧义，无必要新模型全量重复扫，生产前置继续pending。
