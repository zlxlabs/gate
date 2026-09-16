# gate 跨 run 消费侧迁 Silo — 进度

## 2026-09-16 silo_store list

- 当前阶段：implementing / silo_store 列举
- 本段结论：新增 `scripts/silo_store.py list`：`--prefix` 或 `--tier`+`--repo-id`（可选 `--name-prefix`）列出对象键；`--dest` 时按 `artifact_name/relative` 落地。空列表退出 0，列举失败退出 1。夹具键从 B1 canary run `35082772768` 的 `mcli ls --recursive silo/ci-artifacts/` 抄录。
- 关键决策与已否决方案：不改 `get --prefix` 强制补 `/` 的既有语义；跨 artifact_name 前缀扫描走新子命令。未新增独立 `fetch` 命令（list 的 `--dest` 是同一列举的可选物化，消费者是 aggregate 双读与本测试）。
- 下一步唯一动作：aggregate.py 对 terminal 历史与 disposition receipts 做 GitHub ∪ Silo 双读合并，并写表驱动测试。
