# DESIGN-note：误报反证复跑改从宿主镜像取码（gate#373）

## 目标

主审误报的 false-positive 回执，在声明 `SOURCE-MODE=service` 的 runner（ci-i7 / ci-i5 / ci-03）上复跑反证时不再直连 GitHub，因此不会再因外网卡死 300 秒而撤不掉误报；失败时日志能分清「取码/基础设施失败」与「反证不成立」。

## 非目标

- 不改反证命令白名单（`counterevidence_argv.py` 九项 argv 契约）与回执 schema。
- 不改主审流程，不在主审时预存证据内容，不改用 GitHub contents API。
- 不加 fetch 重试。
- 不改 gate-hub 镜像服务本身（`git_mirror_fetch.sh` / `git_source_service.py`）。
- origin 模式主机（ci-m2）这一条路的调度根治另起两张串行卡（gate-hub 加 runner 标签 → gate 改 runs-on），不在本卡做。

## 为什么不是分区 / 删除 / 约定

- **分区**：作业层与外网之间已有宿主取码服务这道分区（gate-hub `docs/designs/git-mirror-consumption.md`），本方案正是把这一步接回分区，不新造机制。
- **删除**：service 模式下删除作业内的临时仓、`git fetch`、缺对象检查与 300 秒超时，改为直接在宿主 bare 镜像上只读执行原样的白名单命令。origin 模式无法删除（没有镜像）。
- **约定**：「service 主机上的作业不许直连 origin」已经是约定（W3a，gate#276），这一步是 10-09（18ea70c）新增时漏接的约定；约定没拦住，所以补一条行为测试锁死，不再只靠约定。

## 方案要点与已否决方案

- **要点**：
  1. 取码步骤按 `$GATE_HUB_GIT_MIRROR_DIR/SOURCE-MODE` 分流，复用同一 workflow 中解析 SOURCE-MODE 的写法与非法值处理（缺失/不可读/非法 → 非零失败）。
  2. service：对 head 调 `git-source-prepare --repository --commit --deadline-epoch`；`match_expectation=absent` 时 base 同样调用。准备必须在加锁之前完成。退出码 2–9 原样映射为 `SOURCE-*` 错误后失败，不回退 origin。
  3. 「加 `consume.lock` 共享锁 → 核对 `refs/demand/<sha>` 解析到同一 commit → 复跑白名单命令 → 放锁」在**同一个 step、同一进程树**内完成（`flock -s` 包住 `issue_receipt.py`）。锁不得跨 step 传递。
  4. 作者提交的命令 argv 一字不改：通过 `--repo-dir` 指向镜像，或通过进程环境（`GIT_DIR`）指向镜像；不得把 `--git-dir` 拼进作者 argv 或回执记录的 argv。设置 `GIT_NO_REPLACE_OBJECTS=1`；镜像属主为 root，作业身份不同时显式传 `safe.directory`。
  5. origin：保留现有临时仓 + `fetch --depth 1`；`timeout` 退出码 124 单独报 `COUNTEREVIDENCE-SOURCE-TIMEOUT`，与其他 fetch 失败区分。
  6. 所有取码/锁/对象类失败使用统一前缀 `COUNTEREVIDENCE-SOURCE-*`，不产出回执；与「反证结果不符」（现有 issue_receipt 拒绝路径）文字上可区分。
- **已否决**：
  - fetch 加有界重试：违反不加重试的规则，只是把同一失败往后推。
  - GitHub contents API 只取 pathspec：pathspec 可以是 `*.py` 等通配；仍然依赖外网，同样会卡。
  - 主审时固化证据内容：申诉的路径与字面量事后才确定。
  - 在作者命令前拼 `git --git-dir=<mirror>`：会改写回执 argv，违反白名单契约（codex 咨询指出）。
  - 取码 step 加锁、下一个 step 复跑：step 的 shell 退出后锁随之释放，保护不到复跑（codex 咨询指出）。

## 关键不变式

1. [实测] ci-i5 镜像 `/opt/gate-hub-cache/git/zlxlabs/agent-config.git` 中已存在 agent-config#5397 的 head `b18403c7…` 与 base `e81efa62…`，均为 commit；`git -c safe.directory=<M> grep -c -F -e OSError <head> -- '*.py'` 在 bare 镜像上 rc=0，耗时 61ms（2026-10-23 ssh 只读实测）。不加 safe.directory 时，以 uid 1000 运行会报 `dubious ownership`。
2. [实测] 失败的两个 runner 槽位 50 / 88 分别属于 ci-i5 / ci-03（`runner/fleet-manifest.json`），两台在 `runner/runner-host-script-targets.json` 中均部署 `git-source-mode/service`。
3. [实测] 三次失败 run 的取码 step 耗时均恰为 300s（GitHub jobs API 的 started_at / completed_at），是被 timeout 杀掉，而不是 git 报错。
4. service 模式下作业不访问 origin：由本卡新增的行为测试锁死（执行该 step 脚本，`git` 包装器记录 argv，断言没有任何一条含 `fetch` 或 `github.com`）。
5. absent 语义需要 base 与 head 两个 commit 都已准备并可读：由本卡新增的真实 git 夹具测试锁死。

## 待验证前提

1. [推断] runner 容器内作业以 uid 0 运行，与镜像属主一致，因此不需要 safe.directory；但仍需显式传入以免依赖这一点。验证入口：合并后在 agent-config#5397 上真实重跑 disposition，看日志。
2. [推断] `issue_receipt.py` 以 `cwd=<bare 镜像>` 执行 `git grep <sha> -- <path>` 时，git 能自动识别 bare 仓。验证入口：真实 git 夹具测试使用 `git init --bare`。

## 验收路径

1. 入口：agent-config PR #5397 的 false-positive 回执，经 `gate-v2-disposition.yml@v2` 复跑。
2. 步骤：gate PR 合并（merge commit）→ 等待 v2 标签经 canary 自动抬升 → 在 agent-config 重新触发该 disposition。
3. 预期：取码 step 在秒级完成；日志中出现 prepare 的 `GIT-SOURCE-PREPARE-V1` 行，没有任何 `github.com` fetch；回执签发成功，或因反证不符被拒且原因可读。
