# 卡 A 进度：gate#278 处置 checkout 路径

- dispatch：`dlg-20261003-154448-78393b`
- base：`f8c16beaeddfe8acc811a6664658e5d457da47dc`
- 分支：`feat/disposition-path-261003`
- 状态：**已修复并自验**；draft PR 待开，生产回执 canary 未跑（需主脑另行授权）。

## 归因

`gate_source.checkout_from_environment()`（`scripts/gate_source.py:307-308`）对空或 `.` 的
`GATE_CHECKOUT_PATH` 直接 `fail(PATH_MISSING, ...)`。`gate-v2-disposition.yml` 的
「Checkout disposition producer」是 10 个 `GATE_CHECKOUT_REF: ${{ job.workflow_sha }}`
调用点里唯一没有声明 `GATE_CHECKOUT_PATH` 的一个，于是 service 模式下整个 job 在取码
一步硬失败，处置入口不可用。`origin` 模式走 `gate_bounded_retry.cmd_checkout()`，
`dest = _workspace()`，静默落在工作区根——所以同一份字节在两种模式下行为不同。

introduced_by_commit：pre-existing（#276/#277 都不碰 disposition 的 checkout env）；
本卡未逐提交 bisect 定位 최초引入者，按 #278 的现状结论处理。

## 改动

1. `.github/workflows/gate-v2-disposition.yml`
   - checkout 步骤新增 `GATE_CHECKOUT_PATH: _gate-disposition-src`；
   - job 级 `SILO_STORE` / `SILO_EXEC` 指向 `github.workspace/_gate-disposition-src/scripts/...`；
   - 步骤内 `Path("_gate-disposition-src/.github/actions/gate-aggregator/convergence.py")`；
   - 步骤内 `python3 _gate-disposition-src/.github/actions/gate-disposition/issue_receipt.py issue`。

   未改 `GATE_CHECKOUT_SPARSE` 的四条路径（它们是**仓库内**相对路径，由
   `checkout_from_environment` 的 `workspace / relative` 拼到子目录下，实测四条全部物化成功）。
   未改 source 生产契约、未加回源 fallback、未动回执 schema 与凭据映射。

2. `tests/test_gate_source.py`：夹具补齐 disposition sparse 列表要求的四个文件（否则
   `checkout()` 的 `sparse path missing after checkout` 会先于路径断言失败）；
   新增 service / origin 两条复现测试，直接跑该步骤自己的 `run` 字节与 env。
3. `tests/test_gate_v2_contract.py`：`assert_workflow_sha_checkout` 的 `path` 语义从
   「`""` 即工作区根」翻转为「必须是非空非 `.` 的子目录」（签名未改，避免制造 TypeError 假红）；
   新增全仓调用点扫描、四处下游消费解析断言、回执步骤真实执行的端到端测试。

## 证据

- base 红验：`SOURCE-PATH-MISSING: GATE_CHECKOUT_PATH must name a destination directory
  below the workspace`（真实 workflow env/argv + 真实 service 镜像夹具）。
- head 绿验：`1365 passed`；`actionlint -color .github/workflows/*.yml templates/*.yml` rc=0；
  `python3 scripts/check_pinned_uses.py` → OK。
- 变异验证（逐处还原）：删 PATH / 改回 `"."` / 只还原 `SILO_EXEC` / 只还原
  `convergence.py` 路径 / 只还原 `issue_receipt.py` 调用 —— 五种都变红，且红的是被改的那条断言。

## 未验证

- 真实业务仓跑一次处置入口（`gate/primary` 误报 finding → 签发回执）：本卡无授权，未跑。
- v2 标签推广：随 canary 自动抬升，本卡不打 tag、不改 caller pin。