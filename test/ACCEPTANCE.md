# IifSift · 验收记录（2026-11-03）

方法：种子缺陷验收——手写一份含**恰好已知缺陷**的 IIF：决策类 4 处（拆分不平差 10.00、缺失 ENDTRNS、引用未定义科目 Mystery Account、TRNSID 重复）＋机械类 2 处（(1,200.00) 括号金额、MM/DD/YY 日期），另植 2 笔干净交易；审计必须不多不少地找出 4＋2。随附 sample.iif 必须零问题返回。

终验结果：**9/9 PASS**（`acceptance_results.txt` 为真跑记录）

- 解析计数：4 交易 / 4 拆分
- 决策问题恰为所植 4 类：duplicate_trnsid、missing_endtrns、unbalanced、undefined_account
- 机械修复：金额格式规范化 ✔、日期规范化 2 处 ✔
- 输出回读勾稽通过（交易数/科目总额到分）
- transactions.csv 中不平交易标记「OFF BY -10.00」、干净交易标记 OK
- sample.iif：3 交易、0 问题，总额勾稽通过

本地包验证：PyInstaller 本机实构建成功（Linux 旁证）；引擎纯标准库。Windows 包由仓库 Actions 构建（待主会话建仓触发）。

样本：`sample.iif` ＋其完整输出集 `sample_out/`（transactions/splits/accounts/customers/vendors.csv、fixed.iif、audit_report.md）。
