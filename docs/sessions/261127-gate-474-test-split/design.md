# gate#474 测试拆分设计

## 分卡领域

按 `docs/sessions/261126-gate-453-split/design.md` 的按领域迁移原则拆分 `tests/test_gate_v2_contract.py`：卡 1 迁 disposition 与 ledger；后续卡迁 quality 与 primary，最终主文件降至 1200 行以内并移除其 `.file-size-exceptions.json` 例外。本卡不修改例外清单。

## 文件与边界

共享非测试辅助放入 `tests/_gate_v2_contract_support.py`；处置契约放入 `tests/test_gate_v2_disposition_contract.py`；ledger 调度、解析、持久化及发布消费契约放入 `tests/test_gate_v2_ledger_contract.py`。新文件上限为单文件 800 行，禁止压行或删测试来满足上限；超限时将边缘用例暂留主文件。

## 不变式与清零路径

拆分前后三个契约测试文件的 pytest 收集数严格守恒为 209，所有原断言与参数原样保留；`test_gate_quality_docs_only.py` 继续从主文件导入 `_run_quality_result_render`。卡 1 仅清理无消费者的 `scripts/review_provenance.py` 及其登记，不动文件大小例外。卡 2 继续按领域迁移，主文件降至 ≤1200 行后再从 `.file-size-exceptions.json` 移除该例外。
