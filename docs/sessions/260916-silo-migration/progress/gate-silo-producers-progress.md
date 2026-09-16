# gate-v2 八类 artifact 迁 Silo — 进度

## 2026-09-16 silo_store 底座

- 当前阶段：implementing / 工具层完成
- 本段结论：新增 `scripts/silo_store.py`（put / put-dir / get / resolve）与 stubbed boto3 测试。键布局 `d<tier>/<repo_id>/<artifact_name>/<相对路径>`；resolve 选 ≤ 当前 attempt 的最大已存在 attempt；无命中退出码 2，查询失败退出码 1。
- 关键决策与已否决方案：boto3 仅在 `connect()` 内 import，workflow 用 `uv run --python 3.12 --with boto3` 临时装，不改 runner 镜像。未把 DNS 解析并进本工具（照卡 A 形态内联到各 job）。
- 下一步唯一动作：把 gate-v2.yml 八类上传步换成 silo put，并给 quality/primary/ocr/gate/ledger 加 MagicDNS 解析步。

## 2026-09-16 magicdns 子命令

- 当前阶段：implementing / DNS 查询进 silo_store
- 本段结论：五份完整 MagicDNS 报文解析若内联进 gate-v2.yml 会撑破 diff 硬顶，故把查询收进 `silo_store.py magicdns`。五个 job 的 workflow 步仍各写一次 `100.100.100.100` 与 `id -u` 写 hosts。第二消费者是 quality/primary/ocr/gate/ledger 五个 job。
- 关键决策与已否决方案：未把写 /etc/hosts 也收进工具（卡面要求 workflow 步可见 id -u）。hosted 失败文案含「tailnet 不可达」。
- 下一步唯一动作：替换 gate-v2.yml 八上传 + 四下载 + 两处解析器，并改契约测试。
