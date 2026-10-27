# Agent 查询与离线 HTML 报告

两个入口读取同一份版本化分析视图。`views.py` 从已有 run、comparison、study
生成事实、质量信息和证据引用；HTML 只呈现这些数据，不重新计算统计结论。
原始工具结果和实验协议保持不变。平台不收录使用方的业务场景或运行数据。

## 一次导出人类与 Agent 两份报告

```bash
perflab report STUDY_ID --kind study --format both --output results/my-study --json
perflab report RUN_ID --format agent --json
perflab report COMPARISON_ID --kind comparison --format agent --output results/comparison.agent.json --json
perflab schema agent-report --json
```

`both` 的 `--output` 是目录，生成 `report.html` 和 `report.agent.json`。
Python 使用同一次 `build_view` 和同一份精简投影生成两者；HTML 顶部的结论摘要
与 Agent 文件共用数值与结论，后续章节保留完整图表、样本和证据链接。
导出结果返回 `paths.html` / `paths.agent`；这两个导出文件都不是权威实验记录。
仅使用既有记录，不启动目标、重新测量或计算另一套统计。

`agent` 不指定输出文件时，精简报告位于 JSON envelope 的 `result`；指定文件时
文件直接保存精简报告，命令返回文件路径。原 `report --format json/text/html`
和 `analyze` 的含义保持不变，原始 `json` 不会被替换成摘要。

Agent 报告保留对象与条件摘要、执行状态、已有联合验收、逐项比较、关键指标、
热点、原有假设、全部类型的质量警示、证据缺口和后续查询入口。不复制原始
样本数组、完整环境、工具日志和构建文件哈希表，也不从热点自动断言根因。
`assessment: null` 表示没有已有联合验收，不能把单 Run 的正确性通过当作性能
PASS；每项比较的回退门和改善证据分别保留。并发竞争条件不会变成隔离串行加速。

`--limit 1..200` 仅用于 `agent/both`，默认 5，限制普通比较及指标、热点、假设
的明细数量。不可比、验收非 PASS、明确失败或回退的比较始终保留，故比较返回数
可以超过 limit。集合中的 `total/returned/omitted` 明确被省略的数量；失败/取消
的来源、候选撤回/替代状态、不可用证据及覆盖缺口不会被解释成成功或零值。
完整原始条件和数据通过 `follow_up` 的 argv 数组按需查询，不能将其拼接为未转义
的 shell 命令。归档证据由已有 `evidence` 接口读取，不在导出时重复计算其哈希。

导出目标必须位于 Store 之外；`both` 中每个实际文件路径也检查符号链接的最终
位置。失败实验仍可生成两份诊断报告，但命令保留原错误和退出码；导出文件存在
不代表实验成功。多文件导出不承诺事务性，写入失败会报错，可能留下已写出的文件。

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

Each view also includes a dimension-wise summary. Multiple saved runs/comparisons
can be collected into a summary view; see [multi-metric reporting](performance-summary.md).

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

复用原有实验存储、统计引擎和 Adapter。阶段格式/关键字映射、应用/OS 切换时间线、
预登记配对 A/B 已有实现，范围见[反馈清单](feedback-implementation.md)。MCP、Web
服务、完整调用树与等待原因追踪仍不属于当前能力，双报告也不补造这些证据。
完整验证见 [验收记录](acceptance.md)。
