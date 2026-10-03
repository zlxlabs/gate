# 主机取码服务（host source service）接入

gate 的三个共享工作流（`gate-v2.yml` / `gate-shadow-v2.yml` / `gate-v2-disposition.yml`）
不再无条件从 GitHub 下载源码。取码方式由**主机声明**决定，声明文件由 gate-hub 部署
（W3b-1），本仓只读。

## 声明

`$GATE_HUB_GIT_MIRROR_DIR/SOURCE-MODE` 恰一行：

| 内容 | 行为 |
| --- | --- |
| `GATE_HUB_GIT_MIRROR_DIR` 未设置或为空 | `origin`：保持既有 GitHub 取码路径（逐字节不变） |
| `origin` | 同上 |
| `service` | 走宿主取码服务：作业零 GitHub 源码下载 |
| 文件缺失 / 不可读 / 其它内容 | **非零失败**，消息含 `SOURCE-MODE` |

判定只在工作流级 `GATE_SOURCE_DECIDE_SCRIPT` 里实现一次，结果写入
`$RUNNER_TEMP/gate-source-mode`；`GATE_SOURCE_BOOTSTRAP_SCRIPT`、
`GATE_CHECKOUT_MIRROR_SCRIPT` 的 service 分支和 `scripts/gate_bounded_retry.py`
都读这一个判定结果，不各自重写规则。`GATE_HUB_GIT_MIRROR_DIR` 已设置而标记缺失 /
不可读 / 内容非法时，`gate_bounded_retry.py` 以 `SOURCE-MODE-UNREADABLE` /
`SOURCE-MODE-INVALID` 非零失败，绝不静默按 `origin` 处理。

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
| caller checkout（quality / primary / ocr / classify / shadow） | 物化前清空工作区（等价 `actions/checkout` 默认 `clean: true`：不属于目标提交的未跟踪 / 忽略 / 只读残留与符号链接全部移除，链接不跟随出工作区）+ 镜像 fetch 全量历史 + `checkout --force --detach`，`actions/checkout` 不执行 | 原内联 mirror 段 + `actions/checkout`（fetch-depth 1） |
| 工具自举（gate-v2 9 处 + disposition 1 处） | `gate_source.py` / `gate_bounded_retry.py` 从镜像 `workflow_sha` 读出 | Contents API 下载 `gate_bounded_retry.py` |
| `pr-size-preflight` / `diff-coverage-advisory` 的 base/head | 对象缺失时经服务从镜像取 | `git fetch --no-tags origin base head` |

`service` 声明的主机上 `actions/checkout` 不再执行，checkout 的副作用审计见
`docs/sessions/git-auto-cache-w3a/progress.md` 同批提交的报告。

## 遥测

* `GATE-SOURCE-V1 {...}`（`scripts/gate_source.py`，stdout）：`mode`、`step`
  （`mode`/`checkout`/`ensure`/`error`）、`source`（`hit`/`cold`/`present`）、
  `reason`、`repository`、`commit_sha`、`elapsed_ms`。
* `GATE-CHECKOUT-MIRROR-V1 {...}`：caller checkout 沿用原行名，service 分支输出
  `{"mode":"service","hit":1,"reason":"service-checkout","elapsed_ms":N}`；
  origin 分支的字段与语义不变。

## 失败字面量

`SOURCE-MIRROR-DIR-MISSING`、`SOURCE-MODE-UNREADABLE`、`SOURCE-MODE-INVALID`、
`SOURCE-MODE-NOT-SERVICE`、`SOURCE-REPOSITORY-REJECTED`、`SOURCE-COMMIT-INVALID`、
`SOURCE-CLIENT-MISSING`、`SOURCE-CLIENT-CONTRACT`、`SOURCE-CLIENT-FAILED`、
`SOURCE-DEADLINE-EXCEEDED`、`SOURCE-LOCK-UNREADABLE`、`SOURCE-LOCK-TIMEOUT`、
`SOURCE-MIRROR-UNREADABLE`、`SOURCE-DEMAND-REF-MISSING`、`SOURCE-FETCH-FAILED`、
`SOURCE-CHECKOUT-FAILED`、`SOURCE-PIN-MISMATCH`、`SOURCE-PATH-MISSING`、
`SOURCE-CREDENTIALS-FAILED`，以及 `gate_bounded_retry.py` 侧的
`SOURCE-SCRIPT-MISSING`。全部由 `tests/test_gate_source.py` 锁住。

## 凭据持久化（与 `actions/checkout` 默认语义等价）

原 caller checkout 是 `actions/checkout` 默认 `persist-credentials: true`，它在工作区
的 `.git/config` 里留下

    http.<GITHUB_SERVER_URL 的 origin>/.extraheader = AUTHORIZATION: basic base64("x-access-token:<github.token>")

caller 仓自带脚本（`make test`、`scripts/gate-quality`、业务仓自己的 `git fetch/push`）
依赖它。service 路径替代 `actions/checkout` 的 5 处（gate-v2 quality/primary/ocr、
gate-shadow-v2 classify/shadow）必须写同样的键和值：值先以
`AUTHORIZATION: basic ***` 占位写入、再就地替换，令牌既不进 argv 也不进日志；
`origin` remote URL 仍不带凭据。工具自举那 10 处（`_gate-action-src` / `_gate-silo-src` 等）
在 `actions/checkout` 时代也不持久化（`gate_bounded_retry.py` 用临时
`GIT_CONFIG_GLOBAL`，用后即删），因此 service 路径同样不写。

## 合并前置

本 PR **必须**在 gate-hub W3b-1 把 `SOURCE-MODE` 部署到全部自托管主机之后才可合并：
`GATE_HUB_GIT_MIRROR_DIR` 已注入而 `SOURCE-MODE` 缺失 / 不可读的主机会以
`SOURCE-MODE-UNREADABLE` **非零失败**（声明已下发、只是文件没落地同样算非法声明），
这些 job 会直接变红。只有 GitHub-hosted 因没有该 env 而安全地走 `origin`。