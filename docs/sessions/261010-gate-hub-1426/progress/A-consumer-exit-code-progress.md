# A 卡进度存档：gate 取码客户端消费端收窄（只认退出码 + 只校验自己消费的字段）

## 里程碑 1：红测试落地
- 当前阶段：implementing
- 本段结论：fake client 失败分支重写为真实 producer 形状（status:"error"、source:null、真实错误码词表、真实 rc 3/4/7/8），新增 crash（rc=1 无协议行）、deadline（rc=8）、long（>512 字符截断）与「不消费字段放行」用例；断言改为 SOURCE-CLIENT-FAILED + exit=<N> + 真实 code，且 SOURCE-CLIENT-CONTRACT 不得出现。
- 关键决策与已否决方案：rc=0 放行用例的 status 值用 "error"（真实 producer 失败值）而非自造 "failed"——既锁定「status 不被消费」，也让 `git grep '"failed"' tests/test_gate_source.py` 零命中，不引入拆字面量。
- 下一步唯一动作：跑 Narrow-Verify 取红输出，然后改 scripts/gate_source.py。
