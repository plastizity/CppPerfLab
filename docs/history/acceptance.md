# 历史实现与验收记录

本文保留 2026-10-14 各轮验收时的实现范围、测试数量和实验记录；其中“当前”及
“尚未实现”均指对应历史阶段，不代表最新能力。实际使用从根目录
[USAGE.md](../../USAGE.md) 开始；后续反馈改进与限制见
[反馈实施记录](feedback-implementation.md)。历史测试数量不作为当前测试总数。

平台使用独立合成 C++ 示例进行公开验收，不收录使用方身份、业务数据或证据。

## 已完成并验证

- Python 3.11 标准库核心、项目本地 zipapp 安装、版本化 JSON CLI。
- Hyperfine、perf-stat、perf-record、Massif 的实际采集与解析。
- Google Benchmark v1.9.4 集成目标的重复 wall/CPU 计时；排除聚合行、区分迭代
  均值与独立重复样本，并拒绝错误输出和多 case 结果。
- 实验身份、原始证据归档、正确性前置检查、超时与进程清理。
- 可比性检查、全部样本保留、CV 与描述性 bootstrap 区间；失败保持错误退出码。
- 预构建候选的多规模实验、经验模型拟合、按规模比较，复用同一实验存储。
- Agent 分段查询、按符号/指标筛选、分页证据读取及哈希校验。
- 单次实验、A/B 比较、规模增长三类离线 HTML，共用统一分析视图。
- 多指标维度总结、多记录引用汇报及同样本 IPC 推导；指令结论独立于耗时。
- 工具路径 JSON 配置、优先级和真实 probe；错误显式路径不触发替代版本。
- 主 Skill 通过 AGENTS.md 发现，包含接入及结果查询指引。

本轮 Python 自动化测试 **44 项通过**，覆盖原有协议/Adapter 行为、新增查询、
证据完整性、HTML 与 JSON 数据一致性、文本转义、不可比报告及工具路径配置。
Node 执行了表格筛选、坐标切换和 JSON 下载的交互逻辑测试。
新增测试覆盖稳定指令减少与噪声耗时并存、IPC 配对条件、未采集的 SIMD/数值
指标、汇报引用及 HTML/JSON 一致性。独立 C++ 指令实验完成两个候选、五个规模
和每点九次重复，增长结果输出有限范围拟合表达式。
独立 C++ 示例已有 20 项 CTest 正确性检查通过。

真实示例结果生成了三类报告，并验证原始证据链接和 Agent 查询。HTML 保留
原比较的 inconclusive 分类，没有重新计算或放宽性能判断。
HTML 结构与交互逻辑已验证；当前环境没有浏览器，未进行浏览器截图验收。

## 复现

```bash
bash scripts/install.sh
source scripts/env.sh
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
cmake --preset profile
cmake --build --preset profile -j 2
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_examples.py
perflab doctor --tool-config tools.example.json --json
```

修改示例工具配置，使其对应已安装工具。实际实验命令见 README；取得返回 ID 后，
按 [统一分析视图](../../guides/analysis-views.md) 导出报告。工具不可用时保留诊断，不伪造结果。
运行数据、报告、本机配置和第三方二进制均不属于公开源码。

## 限制与后续

- 经验增长拟合不是渐近复杂度证明；微基准不是端到端性能。
- 工作目录与资源限制不是不可信代码安全沙箱。
- 当前只提供原始栈链接与热点行，尚无结构化调用树、火焰图或线程时间线。
- 自定义阶段指标、测量后验证、自动交错 A/B 调度尚未实现。
- 自动调优/演化框架、MCP 和独立 Web 服务尚未实现。
- 自动工具下载/安装不在工具路径配置的范围内。
- 本地 Skill 路由不等于全局技能目录注册；公开版本不依赖特定 Agent 安装路径。

## 核心指标扩展验收（2026-10-14）

新增核心能力现有 62 项 Python 测试通过，既有 C++ 样例 20 项 CTest 通过。
实际执行了五规模算法研究、四种同样本 CPU 派生指标查询、Massif 观察和两轮
原生 A/B；旧记录只读查询和原始证据哈希访问通过。可运行命令和能力边界见
[core-metrics.md](../core-metrics.md)。本地原始证据与量化报告保存在
`.perflab/validations/20261014T084014-c1a14621413d/`。

验证目录中的 manifest 记录执行流程，verification 记录最终安装版接口检查；
report 区分指令减少与时间加速，不将单次 heap 观察解释为内存收益。
Skill 格式校验脚本因缺 PyYAML 未运行成功；已检查 frontmatter、引用并实际执行
安装版 CLI。JSON Schema 已检查 JSON 语法，运行时契约有测试；未使用未安装的
第三方 jsonschema 验证器。未安装全局依赖。

## D01–D05 增量验收

76 项 Python 测试与 22 项 CTest 通过，完成三个新 Adapter、显式 Kernel 工作量、
多指标空间增长和原生 A/B 的串行验证。原始数据、策略和量化报告保存在
`.perflab/validations/20261014T091925-9cc2a01e1c2c/`。接口与限制见
[domains-1-5.md](../../guides/domains-1-5.md)。Kernel 时间比较保留 INCONCLUSIVE，模型指标
被拒绝用于实测收益验收；没有安装新工具或更改全局配置。
