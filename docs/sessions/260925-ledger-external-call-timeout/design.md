# Ledger 外部调用超时与步骤进度

## 取证结论

本次修复落在 `.github/workflows/gate-v2.yml` 的 `ledger` job「Resolve v2 ledger artifacts」步骤。其内联 Python 的 `load_current_attempt_jobs()` 和 `load_current_attempt_meta()` 分别运行 `gh api .../jobs` 与 `gh api .../attempts/{current}`；原调用都没有 `timeout=`，非零退出码共用 `Jobs API call failed while attributing a missing current-attempt terminal` 文案。

`.github/actions/review-ledger/build_ledger.py` 不是外部调用来源。2026-09-25 对该文件及唯一依赖 `scripts/scrub_outbound.py` 的实测没有发现 `requests`、`subprocess`、`urlopen` 或 `http.client` 调用；两者执行本地文件读写和字符串处理。实际超时缺口因此修在 resolver 内联脚本，不改 `build_ledger.py`。

历史 run `34745625083` 的 `gate / ledger` 日志显示 build 步骤启动后约 299.4 秒直接被取消，中间没有输出。由于 build 脚本没有外部调用，诱因更像 workflow 外部取消或并发抢占；这只是推断，本次不尝试坐实，也不声称超时修复能解释那次取消。

## 超时选择与失败信息

两个 `gh api` 调用各自设置 30 秒 `subprocess.run(..., timeout=30)`。本仓 workflow 的其他 `gh api` 调用已有 `timeout --foreground 30s` 惯例（例如 `gate-v2.yml` 的 compare API）；沿用相同单次请求上限。两次调用按顺序发生时，等待上限合计 60 秒，明显短于 ledger job 的 10 分钟上限，也为后续下载、构建（已有 1 分钟步骤上限）和上传保留时间。

`subprocess.TimeoutExpired` 会以包含步骤名、API 目标和「超时」的专属 `SystemExit` 文案 fail fast；快速非零退出继续使用既有文案。没有重试或 fallback。

## 验证与步骤进度

resolver 契约测试创建真实临时 `gh` 可执行文件：分别让 jobs 查询和 metadata 查询挂住，以及让二者快速返回非零退出码。挂起测试执行真实 resolver 子进程和 `subprocess.run` 分支，断言约 30 秒时以专属超时文案退出；非零测试断言保留原错误文案。

ledger job 每个现有步骤的 run 内容以 `::notice::step-start: <步骤名>` 开头。GitHub Actions 的 `uses:` 步骤没有内联 shell，因此 build action 前增加一个紧邻的 notice 步骤，用于标记即将开始的 action；不包装或改变原 action 的输入、条件或超时。契约测试从 `_load_workflow()` 解析并逐项检查进度标记。
