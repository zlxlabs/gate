# gate 跨 run 消费侧迁 Silo — 进度

## 2026-09-16 silo_store list

- 当前阶段：implementing / silo_store 列举
- 本段结论：新增 `scripts/silo_store.py list`：`--prefix` 或 `--tier`+`--repo-id`（可选 `--name-prefix`）列出对象键；`--dest` 时按 `artifact_name/relative` 落地。空列表退出 0，列举失败退出 1。夹具键从 B1 canary run `35082772768` 的 `mcli ls --recursive silo/ci-artifacts/` 抄录。
- 关键决策与已否决方案：不改 `get --prefix` 强制补 `/` 的既有语义；跨 artifact_name 前缀扫描走新子命令。未新增独立 `fetch` 命令（list 的 `--dest` 是同一列举的可选物化，消费者是 aggregate 双读与本测试）。
- 下一步唯一动作：aggregate.py 对 terminal 历史与 disposition receipts 做 GitHub ∪ Silo 双读合并，并写表驱动测试。

## 2026-09-16 aggregate 双读合并

- 当前阶段：implementing / aggregate 双读
- 本段结论：`_fetch_terminal_history` 与 `_fetch_disposition_receipts` 改为 GitHub ∪ Silo，冲突键以 Silo 为准。Silo 失败打 `::warning::` 并降级；GitHub 空匹配在 Silo 已补上对应 run 时不再把面板标成不完整。表驱动覆盖 6×2 格；夹具键抄录 canary `35082772768`。
- 关键决策与已否决方案：aggregate 进程用 in-process `silo_store`（boto3 可用时）否则 `uv run --with boto3` 调 `list --dest`，不改 `gate-v2.yml`。receipt 去重键用既有 artifact name（receipt 无 run_id/attempt）。未把 GitHub 读路径拆掉。
- 下一步唯一动作：把 `gate-v2-disposition.yml` 上传改走 Silo，下载 Silo 优先并在键不存在时回退 `gh run download`。

## 2026-09-16 disposition workflow

- 当前阶段：implementing / disposition 迁 Silo
- 本段结论：`gate-v2-disposition.yml` 去掉 `upload-artifact`；receipt 走 `silo_store put-dir --tier d30`；primary-audit 先 `get --key d14/…/primary-review-audit.json`，退出码 2 才 `gh run download`。首个 S3 步前有与 B1 同款 MagicDNS（`100.100.100.100`）。`runs-on` 改为 `[self-hosted, linux, ci]`，因为 GitHub-hosted 到不了 tailnet Silo。
- 关键决策与已否决方案：回退只绑 Silo 退出码 2（键不存在），其它 Silo 错误 fail-loud。未改 receipt 文件名。caller 模板不在本卡允许范围，沿用 `secrets: inherit`。
- 下一步唯一动作：文档与契约测试对齐后跑全量 pytest 与 `check_pinned_uses.py`。
