# Agent 结果查询

先读取 [统一查询与报告说明](../../../docs/analysis-views.md)，按需加载章节，
不要复制或维护另一套指标表。

使用 analyze 默认概览判断 run 状态、正确性范围和质量警示，再通过 section
metrics/hotspots/quality 查询细节。先定位 evidence ID，再用 evidence 的 path
读取有哈希校验的文本片段。索引和路径使用命令实际返回值。

comparison 使用 kind comparison，study 使用 kind study。噪声不稳定或不可比
时解释原因，不选择最快样本替代分类。growth 排名是有限范围经验拟合。

人类交接时导出 HTML；它与 section all 使用同一视图。报告正文不证明根因，
也不自动批准源码修改。保存原始实验目录，让证据链接仍可访问。
