# Agent 结果查询

先读取 [统一查询与报告说明](../../../guides/analysis-views.md)，按需加载章节，
不要复制或维护另一套指标表。

快速交接时先用 `report ID --kind run|study|comparison|summary --format agent --json`
读取精简摘要，再通过其 `follow_up` argv 按需查询。`report --format json` 仍是原始
记录，不是精简摘要。使用 analyze 默认概览判断 run 状态、正确性范围和质量警示，再通过 section
metrics/hotspots/quality 查询细节。先定位 evidence ID，再用 evidence 的 path
读取有哈希校验的文本片段。索引和路径使用命令实际返回值。

comparison 使用 kind comparison，study 使用 kind study。噪声不稳定或不可比
时解释原因，不选择最快样本替代分类。growth 排名是有限范围经验拟合。

多指标汇报读取 [分析总结说明](../../../guides/performance-summary.md)，查询
section summary；跨记录使用 summarize。指令工作量、执行质量、资源与耗时
独立判断，缺失 SIMD/数值数据不得推断。总结记录使用 kind summary。

同时交接给人类与 Agent 时使用
`report ID --kind KIND --format both --output DIRECTORY --json`，取得
`report.html` 和 `report.agent.json`。两者来自同次视图，HTML 顶部摘要与 Agent
文件一致；`--limit` 只限制普通明细，先检查 omitted 和所有异常项，不能把摘要
当作全部样本。执行完成、验收通过与支持加速分别判断。

单独导出 HTML 仍可用；它与 section all 使用同一视图。报告正文不证明根因，
也不自动批准源码修改。保存原始实验目录，让证据链接仍可访问。
