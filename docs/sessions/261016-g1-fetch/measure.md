# G1 #1422 测量记录：pr-size-preflight 本地 fetch base/head 单次 38–150s 的成因与修复对比

任务卡：gate-hub#1422 G1。执行器 dispatch dlg-20261016-034649-780179。
场景口径（卡面约束）：agent-config 规模的 bare 镜像 + 按 caller checkout 形态建工作区 + `git fetch <镜像> refs/demand/<sha>`，`GIT_TRACE2_PERF` 拆段。

## 1. 同构场景搭建

生产形态事实（本仓 `.github/workflows/gate-v2.yml`，quality/primary 两个 job 同形）：

- 工作区由 `actions/checkout@v4` 以 `fetch-depth: 1` 检出 `refs/pull/N/merge` → **shallow、仅 1 个 merge commit、无 alternates**（行 523–524；prime 步自己的 alternates 用完即删，行 368 附近）。
- `ensure_commits` 要补的 base（main tip）与工作区仅有的 merge commit **无共同历史**（depth=1 连父提交都没有，协商时客户端拿不出任何可声明的 have）。

实验环境：20 核 x86_64 桌面机，git 2.43.0（比 CI 的 mini 宿主快，绝对值偏小；对比结论同向放大）。数据源：本机 agent-config clone（22,969 commits，origin/main 2026-10-16）。

```sh
EXP=/tmp/g1-fetch-exp
SRC="$HOME/projects/personal/agent-config"   # 本机 agent-config clone（路径含用户名，不写入公开仓）
BASE=abff2d1767bafed81b4389def66c9a71b3f492cf   # 实验当时的 origin/main tip
HEADPR=6c848aba91e8f710d70932c0e66a8b52c0ce9ab0 # ${BASE}^1，PR head 角色
# bare 镜像（单大包 68MB，含 main 全闭包）+ demand refs（模拟服务端 prepare）
git clone --bare --no-local --quiet "$SRC" "$EXP/svc-root/zlxlabs/agent-config.git"
git --git-dir=…/agent-config.git fetch --quiet "$SRC" "+$BASE:refs/heads/main"
git --git-dir=…/agent-config.git update-ref refs/demand/$BASE "$BASE"
git --git-dir=…/agent-config.git update-ref refs/demand/$HEADPR "$HEADPR"
# 合成 merge commit（-p HEADPR -p BASE），push 进镜像 refs/demand/$MERGE
# 工作区：模拟 actions/checkout fetch-depth:1
git init --quiet ws; git -C ws -c protocol.version=2 fetch --quiet --no-tags --depth=1 \
  "$EXP/svc-root/zlxlabs/agent-config.git" "refs/demand/$MERGE"
git -C ws checkout --quiet --force --detach FETCH_HEAD
# ws 形态核验：is-shallow=true；HEAD=8aa2ce78（merge）；cat-file base → 缺失（预期）
```

## 2. 基线测量：当前生产 fetch 慢在哪

```sh
cd ws; GIT_TRACE2_PERF=2 /usr/bin/time -f "WALL=%es MAXRSS=%MKB" \
  git fetch --no-tags --no-recurse-submodules <镜像> refs/demand/$BASE
```

原始输出（`/usr/bin/time` + trace 关键 region）：

```text
WALL=12.36s MAXRSS=612700KB        # 复跑 12.70s，峰值 RSS ~628MB
child_start ch1 index-pack --stdin          （累计 4.13s 处启动）
pack-objects remote: region_leave | 3.43s | 3.43s | pack-objects | label:enumerate-objects
pack-objects remote: region_leave | 4.12s | 0.69s | pack-objects | label:prepare-pack
pack-objects remote: region_leave | 5.66s | 1.54s | pack-objects | label:write-pack-file
fetch:  region_leave | 12.34s | 12.34s | fetch | label:fetch_refs
fetch-pack: region_leave | 0.0026s | fetch-pack | label:negotiation_v2
```

拆段结论（base fetch ≈12.7s）：

- **协商不是成本**：`negotiation_v2` 2.6ms。
- **~95% 在 upload-pack 服务端**：enumerate-objects 3.4s + prepare-pack 4.1s + write-pack-file 5.7s —— 无共同历史时服务端要现场对整条 main 闭包（~20 万对象）做 delta 压缩；客户端 `index-pack` 与之流水重叠。
- head fetch 在 base 已取得后只要 **0.37s**（共享闭包，增量极小）——即四段串行预算里两段 base 型 fetch 是大头。
- 多包镜像变体（push 触发 `receive.autogc` 后仍为单包 9.23s）与单包同量级，结论不依赖包布局。

候选方案实测（各自 fresh 工作区）：

| 方案 | 耗时 | 峰值 RSS | 判定 |
| --- | --- | --- | --- |
| 现状：逐 sha `git fetch <镜像> refs/demand/<sha>` | base 12.36s + head 0.37s | 612MB | 修的就是它 |
| 合并为一次 fetch（两 ref 一条命令） | 12.69s | 674MB | 无改善，否决（仍是一次整闭包打包） |
| 工作区 alternates 借镜像 objects（`printf '%s\n' <镜像>/objects > .git/objects/info/alternates` 后 `rev-parse`/`cat-file` 校验） | 0.00s | 4MB | 采纳 |

## 3. 修复前后 A/B（真实 `ensure_commits`，非手搓 git）

用任务卡基准提交 4ff7999 的 `gate_source.py`（OLD）与修复后（NEW）各跑一次真实 `ensure_commits(Path(ws), 'zlxlabs/agent-config', BASE, HEADPR)`，配真实契约同形的 `git-source-prepare` 客户端（tests/test_gate_source.py 的 fixture client），`GATE_SOURCE_BUDGET_SECS=180`：

```text
OLD WALL=14.45s MAXRSS=610332KB
GATE-SOURCE-V1 {"elapsed_ms":14428, "step":"ensure", "reason":"ok", ...}
NEW WALL=0.12s  MAXRSS=13760KB
GATE-SOURCE-V1 {"elapsed_ms":92, "step":"ensure", "reason":"ok", ...}
NEW 复跑（命中/skip 路径）：WALL=0.04s，elapsed_ms=4
```

修复后核验：`is-shallow-repository` 仍 `true`（不变深也不变浅）；base+head `cat-file -e` 均可解析；`git diff BASE HEADPR` 正常；alternates 恰一行。桌面机上 14.45s→0.12s；CI 宿主观测基线 38–150s，同机理映射后预期毫秒级（新路径不打包任何对象，宿主 CPU 快慢无关）。

## 4. CI 宿主只读证据（ci-03）

```sh
ssh ci-03 'R=/opt/gate-hub-cache/git/zlxlabs/agent-config.git; …'
```

- 镜像根：`/opt/gate-hub-cache/git`（`git_mirror_fetch.sh` 的 `cache_root` 默认值，runner unit 引用同值）。
- `zlxlabs/agent-config.git`：101MB，**1 个 pack**，无 `objects/info/alternates`，非 shallow，`refs/demand` 932 个。
- 作业工作区在一次性 runner 容器内（`run-ephemeral-runner.sh` + RUNNER_IMAGE），宿主磁盘上无留存可查；工作区形态以 gate-v2.yml workflow 文本为准（§1）。
- 镜像侧留存保障（gate-hub `runner/git_mirror_fetch.sh`）：`DEMAND_RETENTION_SECONDS=604800`（7 天），每次 demand `pin_demand_ref` 重写 ref 文件 mtime 作为 keep-alive；maintain 的 gc 为 `git gc --prune=1.week.ago`。破坏性操作（gc/prune/demand 修剪）持 consume LOCK_EX（G2 卡收窄范围），借对象发生在 consume LOCK_SH 内。

## 5. 修复内容与归属

- `scripts/gate_source.py`：`ensure_commits` 改为「prepare（锁外，逐 sha）→ 单次 consume LOCK_SH 内逐 sha 校验 `refs/demand/<sha>`（既有错误码 DEMAND_REF_MISSING/PIN_MISMATCH）→ `_borrow_mirror_objects` 幂等写一行 alternates → 工作区侧 `cat-file` 校验，不解析则 FETCH_FAILED 响亮失败」。签名与错误码不变；`checkout()` 路径的 `fetch_demand_ref` 原样保留。
- 行为锁测试：`tests/test_gate_source.py::test_ensure_commits_borrows_mirror_objects_without_a_fetch`（双端点可解析 + diff 与上游一致 + 非浅仓 + 无 `fetch --no-tags --no-recurse-submodules` argv 指纹 + alternates 幂等一行）、`…fails_loud_when_mirror_objects_cannot_be_borrowed`（借不到即 FETCH_FAILED）。
- 红验：把借对象两段还原为旧 fetch（单点注入）→ `AssertionError: assert 0 == 1`（alternates 计数断言，断言失败型），还原后 133 项窄验回绿。
- 已否决：合并单次 fetch（§2 实测无改善）；对共享工作区 `--depth`（卡面禁止且会破坏依赖历史的步骤）；本地化 repack（`repack -a` ≈ 整闭包重新 delta，正是被移除的成本）。

残余权衡（记录，不构成回退理由）：工作区在作业期间保持对镜像 objects 的 alternates 依赖；由 demand ref 7 天留存 + pin keep-alive 兜底，作业时长（分钟级）≪ 留存窗口。
