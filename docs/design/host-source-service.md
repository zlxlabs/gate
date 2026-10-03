# 主机取码服务（host source service）接入

gate 的三个共享工作流（`gate-v2.yml` / `gate-shadow-v2.yml` / `gate-v2-disposition.yml`）
的部分取码不再无条件从 GitHub 下载。取码方式由**主机声明**决定，声明文件由 gate-hub 部署
（W3b-1），本仓只读。

**本 PR 的范围只含工具自举与 action 补拉**。caller checkout 不在本 PR：它保持
`actions/checkout` + GitHub 原行为（5 处 step 组与 origin/main 逐字节相等，由
`tests/test_caller_checkout_baseline.py` 锁住），改走宿主取码服务的方案
（保留 `actions/checkout`、step 级 URL 改写到作业私有临时仓）另开卡实施。

**W3c 起，caller checkout 的预热段多一步**：在 `service` 主机上，预热段在取
`consume.lock` 之前先调客户端把目标提交准备进镜像（见下「预热段先准备」）。
`actions/checkout` 步骤本身与它的 `with:` 一个字节都没动。

## 声明

`$GATE_HUB_GIT_MIRROR_DIR/SOURCE-MODE` 恰一行：

| 内容 | 行为 |
| --- | --- |
| `GATE_HUB_GIT_MIRROR_DIR` 未设置或为空 | `origin`：保持既有 GitHub 取码路径（逐字节不变） |
| `origin` | 同上 |
| `service` | 走宿主取码服务：作业零 GitHub 源码下载 |
| 文件缺失 / 不可读 / 其它内容 | **非零失败**，消息含 `SOURCE-MODE` |

判定只在工作流级 `GATE_SOURCE_DECIDE_SCRIPT` 里实现一次，结果写入
`$RUNNER_TEMP/gate-source-mode`；`GATE_SOURCE_BOOTSTRAP_SCRIPT` 与
`scripts/gate_bounded_retry.py` 都读这一个判定结果，不各自重写规则。
`GATE_HUB_GIT_MIRROR_DIR` 已设置而标记缺失 / 不可读 / 内容非法时，
`gate_bounded_retry.py` 以 `SOURCE-MODE-UNREADABLE` / `SOURCE-MODE-INVALID`
非零失败，绝不静默按 `origin` 处理。

客户端调用本身（校验回复、共享 deadline）也只实现一次：工作流级
`GATE_SOURCE_PREPARE_SCRIPT` 定义 `gate_source_prepare`，由两个调用点各自
`eval` 后调用——工具自举与 caller checkout 预热段。
`gate-v2-disposition.yml` 不在本卡范围内，它仍内联同一段调用；
`tests/test_gate_source.py` 按字节锁住「片段」与「内联副本」不得漂移。

## 读取协议（service）

1. 调客户端 `$GATE_HUB_GIT_MIRROR_DIR/git-source-prepare
   --repository <owner/repo> --commit <40hex> --deadline-epoch <int>`；
   stderr 恰一行 `GIT-SOURCE-PREPARE-V1 {...}`；exit 0 = ready（`source` 为
   `hit`/`cold`），2–9 = `SOURCE-*` 失败。**只接受 `zlxlabs/` 仓。**
2. **调完客户端之后**才对 `<镜像>/consume.lock` 取共享锁（有界等待到截止时间）。
   宿主写者需要同一把锁的排他侧，持锁调客户端会死锁。
3. 持锁期间只从镜像**文件系统路径**取对象：
   `git fetch --no-tags <镜像路径> refs/demand/<sha>`（非浅、闭包完整），
   或 `git --git-dir=<镜像> show refs/demand/<sha>:<path>`。
4. 放锁。service 路径任何失败一律非零，**不加重试、不回退 origin**。

预算由 `GATE_SOURCE_BUDGET_SECS`（默认 180s）给出，客户端与锁共享这一截止时间。

## 调用点

| 调用点 | service | origin |
| --- | --- | --- |
| 工具自举（gate-v2 9 处 + disposition 1 处） | `gate_source.py` / `gate_bounded_retry.py` 从镜像 `workflow_sha` 读出 | Contents API 下载 `gate_bounded_retry.py` |
| `pr-size-preflight` / `diff-coverage-advisory` 的 base/head | 对象缺失时经服务从镜像取 | `git fetch --no-tags origin base head` |

caller checkout（quality / primary / ocr / classify / shadow）的 `actions/checkout`
步骤（fetch-depth 1）**逐字节保持原样**，5 处 step 组与 origin/main 基线字节相等
（`tests/test_caller_checkout_baseline.py`）。变的只有它前面那段内联 mirror 预热：
它自 W3c 起多一个「取锁前先请宿主准备」的阶段（见下），`actions/checkout` 拿到的
仓库形状与以前完全一样。

## 预热段先准备（W3c）

实测 184 次预热里 101 次是 `reason=mirror-repo-missing`：镜像里根本没有这个仓，
于是 alternates / haves / depth-1 fetch 全部落空，整个闭包从 GitHub 回来
（boq-gen ≈306 MB、VideoTranscriptAPI ≈88 MB 每 run）。因此 `service` 主机上的
`GATE_CHECKOUT_MIRROR_SCRIPT` 在 `mirror_root` 判定之后、取 `consume.lock`
之前先调客户端准备 `$sha`：

| 阶段 | 行为 |
| --- | --- |
| `origin` 主机 / 无声明 | 不调客户端，预热段与 W3c 之前逐字节一致，`prepare` 记 `skipped` |
| `service` 主机 | 取锁前调客户端；客户端失败即**非零失败**（`SOURCE-*`），不降级 origin |
| 客户端失败 | 不发 `GATE-CHECKOUT-MIRROR-V1` 结果行、不留半成品 `.git`、不 fetch origin |
| classify 站点 | 用 `GATE_CHECKOUT_REPOSITORY` / `GATE_CHECKOUT_REF`（= `zlxlabs/gate`@workflow_sha）准备 |

该阶段不是软 miss：它失败时 EXIT trap 暂时摘掉，让 `SOURCE-*` 直接把 step 打红。
客户端调用必须在取锁之前（宿主写者要 `consume.lock` 的排他侧），
由 `tests/test_gate_checkout_mirror.py` 的假客户端「探排他锁」锁住。

## 遥测

* `GATE-SOURCE-V1 {...}`（`scripts/gate_source.py`，stdout）：`mode`、`step`
  （`mode`/`checkout`/`ensure`/`error`）、`source`（`hit`/`cold`/`present`）、
  `reason`、`repository`、`commit_sha`、`elapsed_ms`。
* `GATE-CHECKOUT-MIRROR-V1` / `GATE-CHECKOUT-BYTES-V1`：caller checkout 的既有
  遥测。W3c 只给 `GATE-CHECKOUT-MIRROR-V1` **追加**两个字段：`prepare`
  （`hit`/`cold`/`skipped`）与 `prepare_ms`；`hit`、`reason`、
  `origin_fetch_pack_bytes`、`object_type` 语义不变。

## 失败字面量

`SOURCE-MIRROR-DIR-MISSING`、`SOURCE-MODE-UNREADABLE`、`SOURCE-MODE-INVALID`、
`SOURCE-MODE-NOT-SERVICE`、`SOURCE-REPOSITORY-REJECTED`、`SOURCE-COMMIT-INVALID`、
`SOURCE-CLIENT-MISSING`、`SOURCE-CLIENT-CONTRACT`、`SOURCE-CLIENT-FAILED`、
`SOURCE-DEADLINE-EXCEEDED`、`SOURCE-LOCK-UNREADABLE`、`SOURCE-LOCK-TIMEOUT`、
`SOURCE-MIRROR-UNREADABLE`、`SOURCE-DEMAND-REF-MISSING`、`SOURCE-FETCH-FAILED`、
`SOURCE-CHECKOUT-FAILED`、`SOURCE-PIN-MISMATCH`、`SOURCE-PATH-MISSING`，以及
`gate_bounded_retry.py` 侧的 `SOURCE-SCRIPT-MISSING`。全部由
`tests/test_gate_source.py` 锁住。

## 合并前置

本 PR **必须**在 gate-hub W3b-1 把 `SOURCE-MODE` 部署到全部自托管主机之后才可合并：
`GATE_HUB_GIT_MIRROR_DIR` 已注入而 `SOURCE-MODE` 缺失 / 不可读的主机会以
`SOURCE-MODE-UNREADABLE` **非零失败**（声明已下发、只是文件没落地同样算非法声明），
这些 job 会直接变红。只有 GitHub-hosted 因没有该 env 而安全地走 `origin`。
