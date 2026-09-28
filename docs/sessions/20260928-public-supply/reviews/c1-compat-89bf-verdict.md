## 审查结论

**需修复后复审。**

审查对象固定为 `85916ed5c4f9d181fec866f15d1bb5f58111c9c8..89bf4abc6dd0ef2dceab931822bdebab2c36a899`，共 12 个文件。以下结论只针对该冻结范围，未跟随后续提交。

本轮已检查这些不变式轴：可信仓库 ID 的来源选择；managed 与 legacy 凭据路径及失败处理；wrapper 和 aggregate 的真实子进程 argv/env；四个 Silo job、15 个调用点与 caller repository ID 命名空间；quality 的密钥配置；disposition 和 standalone 兼容；Gate 的 fork、hosted、draft 等 job 条件；真实 profile 文件读取与 S3 payload 断言；新增抽象、日志可检索性及行数预算。没有 SQL 或 UPDATE/INSERT/DELETE。

`failure-visibility: p2-only`
failure-visibility: p2-only

## P2：aggregate 未保留 managed 默认凭据源

**位置：** [.github/actions/gate-aggregator/aggregate.py](/home/zlx/projects/personal/gate-worktrees/priority-public-supply-260928/.github/actions/gate-aggregator/aggregate.py:2161)  
**违反条款：** 冻结 spec 的“C1 兼容修订卡”要求 aggregate 使用同一凭据源并保留 standalone 兼容，[design.md](/home/zlx/projects/personal/gate-worktrees/priority-public-supply-260928/docs/sessions/20260928-public-supply/design.md:25)。

`_silo_cli()` 复制当前环境后，只为 `RUNNER_TEMP` 设置默认值，没有为 aggregate 子进程设置凭据源。若 standalone aggregate 在 managed profile 环境中运行、但调用环境没有 `SILO_CREDENTIAL_SOURCE`，wrapper 会按 [silo_exec.sh](/home/zlx/projects/personal/gate-worktrees/priority-public-supply-260928/scripts/silo_exec.sh:16) 将未设置 selector 解释为 `legacy-env`。旧版 aggregate 则明确传入 `--managed-profile`。

**失败场景：** selector 缺省、profile 可读而 AWS env key 不存在时，aggregate 无法读取 Silo 历史。相关路径会返回降级结果或记录 history unavailable，而不是读取原有的 managed 数据；这会破坏支持的 standalone aggregate 行为。当前 reusable workflow 有全局 selector，因此此问题针对 selector 缺省的 aggregate 调用。

**P1 两问：** 受支持的 standalone 用法中可触发，但当前生产调用频率未实测；失败会降级历史读取并留下 unavailable/incomplete 信息，没有证据表明会造成数据丢失、静默错误或崩溃。因此为 P2，不是 personal 风险等级的 P1。

**有界修复任务：** 仅在 aggregate 子进程环境缺少 selector 时，将默认值设为 `managed-profile`；显式 `legacy-env` 和 `managed-profile` 必须原样保留。新增实际 wrapper 子进程测试，断言缺省 selector 时传出的 selector、AWS env 与 managed argv；保留显式 legacy 路径测试，并验证 standalone 源码路径默认值。

本轮既有日志记录 focused 597 项、full 1272 项通过，但 aggregate producer 测试显式设置了 selector，不能覆盖上述缺省场景。未运行测试。

## 剩余验证盲区

profile 尚未部署；quality runner 的物理不可见性、owner/mode 与可信 runner 的可读性仍待真实环境验收。effective group / parent policy、Silo ACL、all28 隔离及 active Claude adapter 探针也未完成。这些是 spec 明确的部署前置条件，不作为本轮源码 finding，也不据此代替运行证据。实际 private/public/fork/Dependabot/draft→ready 矩阵尚未运行；本轮只确认 diff 未改变现有 job skip 条件。无新增 SQL 风险或无第二消费者的抽象。
