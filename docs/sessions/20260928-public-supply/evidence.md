# 证据：gate#249 受管 Silo 供给设计

## 源码与发布事实

- 基线：`85916ed5c4f9d181fec866f15d1bb5f58111c9c8`，gate worktree HEAD、`origin/main` 与 `git ls-remote origin refs/heads/main` 一致。PR #251 merged `08a3baa16650e314f05d4e3aea9ec3631cad3760`，2026-09-26；canary run `36400763527` success。均不代替真实 public/private/fork/draft/Dependabot 矩阵。
- `scripts/silo_store.py:85-100` 的 `connect()` 集中取两个 AWS key 与 endpoint。`.github/workflows/gate-v2.yml:674-677,1126-1129,1627-1630,1946-1949` 是 primary/OCR/gate/ledger job key 注入；15 个 `AWS_ACCESS_KEY_ID` precheck 分布在这四个 job。`aggregate.py:2150-2165` 以 `SILO_ENDPOINT` 判断 configured 并直接 subprocess 调 trusted store。
- `quality` job 无 Silo key env；bundle producer 写 `GITHUB_OUTPUT`，`needs.quality.outputs.ledger_input_bundle` 交给 trusted ledger。gate 仓 no-artifact 测试禁止 GitHub artifact actions。
- Caller 文件 `templates/caller-gate-v2.yml:110-112` 与 README:32-33、131-139 仍要求 caller keys。另一个 consumer 不能忽略：`.github/workflows/gate-v2-disposition.yml` 的 `workflow_call.secrets` 声明与 `control` job (`runs-on: [self-hosted, linux, ci]`) 把 caller keys 注入 AWS env，经 `silo_exec.sh` 在两处 get/put；无 host-managed profile。C1 若全局改 env 默认会破坏该路径。
- gate-v2 `workflow_call.secrets.SILO_ACCESS_KEY/SECRET_KEY` 当前均 `required: false`。C1 保留这两个声明和旧 caller mapping；移除它们会让仍映射 key 的 reusable-workflow caller 在 GitHub 解析阶段失败。Public template mapping 的移除单独归 C2。
- 使用锁定 gate-hub registry 与 `resolve_policy.py` 对 `zlxlabs/CapsWriter-ASR-Server` 实际解析：chain 为 `codex-sub` (codex)、`claude-deepseek-v4-pro` (claude)、`claude-qwen-3-7-plus` (claude)，advisory/shadow 为空。当前目标没有 active OCR；两个 active Claude adapter 未作真实 tool sandbox probe，不能以 Codex 单例作 rollout 依据。

## 合成 Codex profile 探针（非生产）

- 使用本地镜像 ID `sha256:8677ef744305050357371f653febbf32cef0058a1e7878ff952fa0d0d11dc856`，Codex CLI `0.146.0`；host CLI 版本不代替镜像。真实 `build_codex_readonly_profile_flags /workspace` flags 仅允许 workspace 读，deny `/opt/review-auth/**`、`/proc` 与 auth/state 路径，network disabled。marker 为合成值；未挂生产 review-auth。
- 实际 `codex exec --json` command event 的 workspace 正控读到 22 bytes 非空内容。direct `/opt/review-auth/marker.txt` 工具输出无 marker（cat rc 0），同一工具命令写入为 `Read-only file system`、write rc 1；raw shell 能读写 marker，host 回读未变。
- `/proc` probe 脚本固定为 `/bin/sh /workspace/proc_alias_probe.sh`，只读 `/proc/self/root/opt/review-auth/marker.txt` 与 `/proc/1/root/opt/review-auth/marker.txt`，只输出 rc/是否非空；不扫描 `/proc`。真实 `command_execution` 完成 exit 0，聚合输出 `SELF_RC=1 SELF_CONTENT=empty`、`PID1_RC=1 PID1_CONTENT=empty`。同一 synthetic fixture 与 image/security 参数下另一个 raw-shell 临时容器两 alias 均 `rc=0`、内容 nonempty；host marker 36 bytes 且未变。Codex exec 与 raw-shell 正控是两个短命容器，不能写成同容器对照。
- 可复查文件：`/tmp/gate-hub-proc-alias.EIYJ11/workspace/proc_alias_probe.sh`、`output/events.jsonl`；前序 direct profile 证据 `/tmp/gate-hub-codex-marker-probe.iF8n0W/`。只有 synthetic marker，未取/打印真实 secret。此证据只支持当前 Codex 实际 tool 路径的两个 alias，不证明 OCR/Claude 或所有 proc/state 路径。

## 尚未验收

- public same-repo trusted review 无 caller Silo secret、private compatibility、外部 fork 有效测试与显式 skip、Dependabot、draft→ready 均未在本轮真实运行。Hosted consumer 不会有 managed file；须核其现有 Silo fail/unavailable/skip 终态不被 C1 flag 改坏。
- `/opt/review-auth/silo.json` 尚只是固定路径候选；生产 managed profile 未创建，trusted/quality runner 各自可见性与 file owner/mode 未实测。需用户授权后由 owner 准备部署，再按 C1 前置验收。
- Codex CLI 0.146.0 synthetic probe 不覆盖两条 active Claude tool adapters、其他 repo/tier 的 active reviewer 或非缓存 host state；这些均为 merge/@v2 前置。Silo server-side repo/prefix ACL 未由本任务复核；disposition env mode 保持不变且需要独立决策其长期迁移。无生产槽启停、生产配置修改或 secret 读取。

## 独立顾问与根侧裁决

- Codex consult event `20260928-191544-codex-09e2bb`，记录与报告位于 `record.json` 与 `report.md`，exit 0。顾问认可 managed/env 互斥边界、五个 connect consumer 与 aggregator argv 需测，也指出缺 profile 的 hosted 路由不可由绿色必需门禁掩盖；其隔离 checkout 缺 `.git`、探针原件不可见，故不能证明读到的是锁定 SHA 或原始 Codex 事件。
- 根侧在 gate checkout `85916ed5c4f9d181fec866f15d1bb5f58111c9c8` 直接复核 `.github/workflows/gate-v2-disposition.yml`，确定 `control.runs-on` 是 `[self-hosted, linux, ci]`（不是早先笔记中的 `ubuntu-latest`）。按此源事实修正本设计；disposition 保持 caller-key legacy env 入口。后续 runtime/测试另卡 #262，引用 #249。
