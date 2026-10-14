# Agent 查询与离线 HTML 报告

两个入口读取同一份版本化分析视图。`views.py` 从已有 run、comparison、study
生成事实、质量信息和证据引用；HTML 只呈现这些数据，不重新计算统计结论。
原始工具结果和实验协议保持不变。平台不收录使用方的业务场景或运行数据。

## 工具路径配置

```bash
bash scripts/install.sh
source scripts/env.sh
cp tools.example.json tools.local.json
perflab schema tools --json
perflab doctor --tool-config tools.local.json --json
```

编辑 `tools.local.json` 使路径符合本机安装。相对路径按配置文件目录解析，
不展开 shell 变量。配置由工具 ID 索引：外部工具使用 executable；
Google Benchmark 使用 build_prefix，只提供构建提示，不代表目标已经集成。
缺失的工具可从配置中删除，让环境/PATH 参与发现；显式配置了错误路径时
不会自动改用其他版本。文件存在或目录存在只通过路径检查，仍需实际 probe。

可为每条命令指定 `--tool-config`，或设置：

```bash
export CPPPERFLAB_TOOL_CONFIG="$PWD/tools.local.json"
```

优先级：配置文件 → 每工具环境覆盖 → 工具目录/PATH。配置只管理路径和
诊断，不执行下载、安装或构建。doctor 的 `tool_configuration` 显示解析路径；
`capabilities.tools` 显示实际采集能力。全局 Google Benchmark probe 报
integrated_target_required 属于正常诊断，需要通过带目标的 plan/run 验证。

## Agent：渐进读取

```bash
perflab analyze RUN_ID --json
perflab analyze RUN_ID --section metrics --metric time.wall --json
perflab analyze RUN_ID --section hotspots --symbol kernel --offset 0 --limit 10 --json
perflab analyze RUN_ID --section quality --json
perflab analyze RUN_ID --section evidence --limit 20 --json
perflab evidence RUN_ID --path measurement-000/perf-report.txt --offset 0 --limit 80 --json
perflab analyze COMPARISON_ID --kind comparison --json
perflab analyze STUDY_ID --kind study --section growth --json
perflab analyze RUN_ID --section all --json
perflab schema analysis --json
perflab schema query --json
```

用真实返回的 ID。默认 analyze 返回概览及质量摘要；完整分析使用 section all。
旧版 analyze 的 facts/hypotheses 现在通过对应 section 获取；原始 report JSON
格式及 `comparison.analyze` Python 函数保持兼容。该 CLI 默认输出变化应由调用者
显式迁移，不再假定 analyze 默认包含全部热点。

列表 section 返回 data、pagination.total/next_offset；空匹配是空列表，
不存在记录或非法筛选是错误。metric 只用于 metrics/growth/comparisons；symbol
只用于 hotspots。默认 limit=20，列表最大 200；完整视图不接受筛选或分页。

证据项包含 `id = run_id:path`、相对路径、字节数和归档 SHA-256。
evidence 命令按行分页，读取前验证哈希，拒绝越界路径和已变更文件；二进制文件
交由所属原生工具读取。HTML 文件链接只连接原始文件，不会执行哈希验证。

质量信息区分 info/review/error。样本与噪声政策复用 comparison；CPU 样本数
单独展示，不能当重复计时样本数，也不会输出统一“可信度分数”。
hotspots 的 sample 表示采样重复；不同重复的百分比不能相加。

## 人类：三类视图

```bash
perflab report RUN_ID --format html --output results/run.html --json
perflab compare BASELINE_ID CANDIDATE_ID --metric time.wall \
  --format html --output results/comparison.html --json
perflab report COMPARISON_ID --kind comparison \
  --format html --output results/comparison-again.html --json
perflab report STUDY_ID --kind study --format html --output results/growth.html --json
```

在浏览器打开导出的 HTML 即可。无服务器、CDN 或前端依赖；原始数据快照
内嵌在报告，支持下载与 Agent 全量查询一致的 JSON。

- 单次实验：条件、正确性、指标范围、重复样本图、热点筛选、事实和待验证假设。
- A/B：两组全部样本、中位数、CV、原有 bootstrap 区间与分类、构建差异。
- 规模：分语义绘图、线性/对数切换、每点波动信息、已有模型拟合误差与限制。

交互包括符号/证据表筛选、详情展开、规模图坐标切换和 JSON 下载。
样本图不把组内序号当配对实验。不同 metric name/unit/method/scope 分开绘制。
本轮没有完整调用树或火焰图；完整栈通过原始证据查看，不生成估计栈。

HTML 必须指定 output，且不得写入权威实验存储，以免覆盖证据。导出目录位于
实验存储之外；原始证据链接是相对路径，移动报告需保持相对目录关系。
报告中的工具文本被转义，不作为 HTML/JavaScript 执行。

不可比的 compare 仍退出 7，并保存带 error 的 comparison 记录；指定 HTML 时
可查看差异，但不生成加速比。噪声导致 inconclusive 时不宣称收益。
失败实验可以导出诊断报告；命令仍传播原错误退出码，不转换为成功。

## 实施门与边界

1. 共享视图与 Schema → 分段查询/证据查询 → HTML 渲染。
2. 三类真实独立示例 → JSON/HTML 数据一致性、失败与不可比路径验收。
3. 本地工具配置 → 实际 probe 与错误路径测试。

复用原有实验存储、统计引擎和 Adapter。MCP、Web 服务、时间线、结构化调用树、
自定义阶段指标解析及自动 A/B 调度属于后续扩展，未注册为已实现功能。
完整验证见 [验收记录](acceptance.md)。
