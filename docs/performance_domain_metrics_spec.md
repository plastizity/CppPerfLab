# Performance Domain & Metrics Specification

规范版本：0.3.0（首轮规格，尚未全部实现）
规格日期：2026-10-14；实现状态核对日期：2026-10-27
本轮范围：D01–D12、D14 完整规格；D13/D15/D16/D17 暂不纳入实施。

本规范定义 CppPerfLab 性能研究领域、指标语义、证据条件与验收要求。
它是后续实现和测试的依据，不代表文中列出的指标已经可以采集。
实施顺序和进度另见本地计划 `performance-expansion-plan-local.md`。
规格版本记录指标语义和契约；实现状态可独立更新，不改变 v0.3.0 的语义。现有接口以 [protocol.md](protocol.md)、CLI schema 和各专项实现说明为准；本文定义的目标规则或指标只有实现后才能通过 CLI 使用。

## 1. 范围、架构与实现状态

继续复用唯一的 `lab.run`、Execution Engine、Adapter Registry、Store、comparison、study 与 views。
领域是分析标签，一个指标可以属于多个领域；不按领域建立独立执行器或结果库。
Adapter 负责采集和解析，领域知识负责解释证据，comparison 负责比较与统计。
所有平台逻辑独立于业务、几何、材料、输入范围及示例算法。

状态使用以下四类，必须落实到具体能力，不能笼统标记整个领域完成：

| 状态 | 含义 |
|---|---|
| 已实现且真实验证 | 存在可执行实现及对应真实实验；验证范围受工具、目标和环境限制 |
| 已实现但待验证 | 代码存在，当前要求的环境或目标尚无真实证据 |
| 仅规格设计 | 本文定义了语义或接口，尚未实现 |
| 尚未开始 | 尚无完整规格或实现 |

当前已有 Hyperfine、Google Benchmark、perf-stat、perf-record、Massif、Callgrind、DHAT、Cachegrind Adapter；已有单轴规模实验、经验增长拟合、描述性统计和非配对 Bootstrap 比较。另有预登记串行或显式并发竞争的配对 Study；其区块统计及适用范围见 [paired-studies.md](paired-studies.md) 和 [concurrent-paired.md](concurrent-paired.md)。
既有验证范围见 [acceptance.md](acceptance.md)，不能据此推断所有硬件事件在本机可用。
同样本 IPC 推导已有实现；统一 Metric Registry、六态证据协议和单指标可配置验收已有核心实现；Suite 汇总仍待实现。当前可调用范围及限制见 [core-metrics.md](core-metrics.md)。
本规范文档本身不构成实测证明；后续核心实现及测试/实测记录独立维护，不将文档覆盖计为工具覆盖。

## 2. 公共指标与证据契约

### 2.1 指标类别与记号

- **Outcome（O）**：最终结果，例如端到端时间、吞吐、峰值内存。
- **Diagnostic（D）**：原因线索，例如误预测、指令数、缓存 miss；不自动赋予性能收益方向。
- **Efficiency（E）**：资源效率，例如 IPC、带宽利用率；提高效率不保证端到端更快。

表格中的 `O/D` 等表示多种用途，比较时仍须指定本次用途。
`T` 是明确范围内的墙钟秒数，`I` 是退休指令数，`C` 是同范围 CPU cycles，`Q` 是已定义且通过正确性检查的操作数量，`B` 是注明层级的字节数。
“操作”可以是一次查询、一次求解或一个数据元素，必须由实验事先定义，不得在候选之间改变。
所有比率的分母必须有效且非零；分母为零时返回不可计算原因，不填零、无穷大或默认值。

公共结果指标：

| Metric ID | 类别 | 定义 | 单位与范围 |
|---|---|---|---|
| `time.wall`（已有） | O | 指定进程边界内的结束时间减开始时间 | 现有工具分别为 seconds/ns；包含启动，method 不同不能直接混比 |
| `time.iteration.wall`（已有） | O | Google Benchmark 一次重复内的迭代平均墙钟时间 | seconds；注册 benchmark 的计时区间 |
| `time.iteration.cpu`（已有） | O/D | benchmark 报告的每迭代 CPU 时间 | seconds；线程计时模式必须保留 |
| `time.cpu`（已有） | D/O | perf task-clock 记录的任务 CPU 时间 | ns；记录线程/进程包含关系 |
| `workload.throughput`（设计） | O | Q/T | operation/s；Q 与 T 的工作范围一致 |
| `memory.rss_peak`（设计） | O | 观测窗口内驻留内存最大值 | byte；注明进程树、采样或系统高水位口径 |

跨工具单位可转换不等于语义可比较。秒与纳秒可换算；进程时间与迭代时间、CPU 时间与墙钟时间不能互换。

### 2.2 Metric Registry 目标记录

可查询目录的目标契约包含下列字段；核心目录已实现，实际可查询字段及范围见 [core-metrics.md](core-metrics.md)，这些字段不直接作为 request 配置项。

| 字段 | 契约 |
|---|---|
| `metric_id`, `definition_version`, `name`, `description` | 稳定 ID 和定义版本；破坏语义的改动采用新版本或新 ID |
| `domains`, `categories` | Domain ID 列表及 O/D/E 类别；不绑定单一工具 |
| `unit`, `formula`, `operands` | 单位、数学定义、操作数及分母条件；公式不可执行任意代码 |
| `scope`, `collection_method` | 进程/线程/region/设备、时间窗口和采集方法 |
| `source_capability` | 所需计时、事件、trace 或目标计数能力；由 Adapter 声明实际绑定 |
| `availability`, `reliability` | 运行时探测结果和适用条件；目录本身不能承诺可用 |
| `aggregation_rules` | 可求和的维度、比率加权规则、重复样本统计口径 |
| `comparison_constraints` | 必须一致的口径、配置及需要明确承认的实验处理变量 |
| `expected_direction` | lower/higher/none；诊断指标默认 none |
| `raw_evidence_reference` | 实际观测引用 run ID、sample ID、原始路径和校验摘要；目录记录只声明引用要求 |

发现工具映射时读取现有 Adapter 的 `metrics`、options 和真实 probe；不能根据同名字段自动合并来源。
新增工具需提交来源语义映射及测试。未知指标保留原始证据，但不得自动纳入已知指标评分。

### 2.3 六种证据状态

六态协议及旧记录读取兼容已有核心实现；状态的取值和来源按指标能力分别核验，详见 [core-metrics.md](core-metrics.md)。

| 状态 | 定义与数值规则 |
|---|---|
| Measured | 工具直接观测，或同窗口完整观测量的确定性单位转换/代数推导；派生量必须记录 operands 和 derivation |
| Estimated | 从采样外推、非完整覆盖或假设估计，记录估计方法和误差来源 |
| Modeled | 模拟器、静态分析或经验模型输出，记录模型和输入；不能当作硬件实测 |
| Unsupported | 本机/目标/权限无法提供所需能力，记录原因，不提供有效数值 |
| Missing | 能力可能存在，但本次未获得数据；不提供有效数值 |
| Invalid | 已采集但违反有效性契约；原始数据留存，不参与评分 |

状态与采集方法分别记录。采样占比本身可为 Measured；由占比推算时间为 Estimated。
Cachegrind 的模拟 cache 行为为 Modeled；llvm-mca 的吞吐预测为 Modeled。
状态不能表示“结论确定”：Measured 仍可能噪声大、无因果解释或不可比较。
现有 `status=observed` 不直接改义；新增记录使用 `evidence_state` 和定义版本，旧记录缺该字段时按已知原有 method 解释，不能批量伪造新状态。

### 2.4 单位、聚合与兼容性

- 规范展示采用 s、byte、count、ratio、operation/s、FLOP/s；现有编码如 seconds、ns、bytes 保留并显式映射。
- 1 ns = 10^-9 s；1 KiB = 1024 byte；1 kB = 1000 byte；百分比展示值 = ratio × 100。
- 对重复实验保存全部样本，报告 n、均值、中位数、样本标准差、CV 和指定分位数。n=1 时样本标准差/CV 为未定义。
- 跨不重叠区间可加总计数；比率由总分子/总分母计算，不能默认平均百分比。重复实验的样本比率统计与合并计数比率分别命名。
- 峰值不能相加当作系统峰值。多进程系统峰值必须从同步时间序列定义；峰值之和仅可作为注明含义的上界。
- 同一公式的事件必须匹配 sample、scope、窗口、事件选择与 privilege filters；不得拼接不同 run 的指令数和 cycles 得到 IPC。
- 硬件计数记录 event 名称/编码、PMU、CPU 型号、工具版本、time_enabled/time_running 或运行比例。复用现有比较策略时保留其运行覆盖门；不能把现有 99% 门限当成跨平台准确性保证。
- 默认要求相同工具方法、工作负载、输入身份、环境及测量边界。跨工具比较必须有显式语义适配和验证；仅做单位换算不放行。

### 2.5 实验记录与统计

复用现有 run 保存原始样本、UTC 时间、工具版本、环境、输入哈希、二进制/源码身份、编译参数、线程配置、亲和性、预热、重复数和原始证据。
补齐记录实验顺序、处理变量、统计策略、异常值策略、限制以及所需事件的未采集原因。
默认保留全部样本；排除样本必须有预先规定的有效性规则和逐条理由，不可因结果不利而删除。

普通比较使用中位数、CV 和描述性非配对 Bootstrap；7 样本、10% CV、5% 变化只是示例筛查策略，不是本规范对全部领域的统一验收门。已有策略支持预先配置样本数、噪声限制、目标变化和回退限额；配对 Study 另按区块计算描述性 Bootstrap。目标契约还要求允许预先配置统计策略和置信水平，尚未暴露的选项不视为已经实现。
分位数记录算法；尾延迟需要足够事件，不能用少数重复估计可靠 P99。
Bootstrap 必须记录抽样单位、次数、随机种子和区间方法；有时间相关性时不能将同一次执行内的事件当作独立重复。

## 3. 通用收益与机器验收设计

### 3.1 收益向量

对正确性通过且可比的相同工作负载，定义：

- Speedup = median(T_baseline) / median(T_candidate)。
- RuntimeReduction = 1 − T_candidate/T_baseline。
- ThroughputImprovement = throughput_candidate/throughput_baseline − 1。
- MemoryChange = M_candidate/M_baseline − 1；CPUResourceChange、EnergyChange 同理，但分别固定 CPU 时间、能量及设备范围。
- 每项同时保留绝对差。基线为零时相对变化不可定义，只报告绝对差。
- 正确性失败时不产生有效收益评分。IPC、cache miss 和内存字节不能相加成综合分数。

SuiteSpeedup = exp(Σ w_i ln(Speedup_i) / Σ w_i)。场景、正权重和重要场景回退限额在比较前登记并归档。
所有预定场景必须保留且可比；缺失、错误或不可比场景阻止有效 Suite 评分，不删除场景重算。
总体得分不能覆盖重要场景的回退否决。Suite 实现与预登记接口尚待开发。

### 3.2 公共验收规则 G

每域继承以下规则，领域规则只增加必要条件。以下规则 ID 是目标机器规则的规格；现有 comparison 与 assessment 实现了部分相关验收门，但不表示这些规则 ID 均已有独立执行器，实际范围见 [core-metrics.md](core-metrics.md) 和 [joint-assessment.md](joint-assessment.md)。

| Rule ID | 判断内容 | 结果 |
|---|---|---|
| G-CORRECT | 两个候选都满足声明的正确性/精度约束 | 明确失败为 FAIL；未执行/证据缺失为 INCONCLUSIVE；禁止评分 |
| G-COLLECT | required_metrics 全部有可用证据 | 能力不可用为 UNSUPPORTED；未取得为 INCONCLUSIVE；无效数据不得评分 |
| G-VALID | 状态、单位、分母、覆盖、窗口及原始证据有效 | 无效测量为 INCONCLUSIVE；若违背明确的数据完整性验收要求则 FAIL |
| G-COMPARE | 工作负载和测量语义通过可比性检查 | 不满足为 INCONCLUSIVE，保留不可比原因，无收益评分 |
| G-TARGET | 预定结果目标满足，且统计可信度满足策略 | 区间完全达到目标为 PASS；完全位于失败范围为 FAIL；跨界为 INCONCLUSIVE |
| G-REGRESSION | 所有硬性资源上限和重要场景回退限额满足 | 确认违反为 FAIL；证据不足为 INCONCLUSIVE |

总状态优先级：已确认硬门失败 → FAIL；否则必要能力不支持 → UNSUPPORTED；否则证据不足 → INCONCLUSIVE；所有必选门满足才为 PASS。
可选诊断指标缺失不必阻止时间目标验收，但对应瓶颈解释必须标为未证实；不得因此写成整个领域已验证。

目标验收配置示意（不是当前 CLI 请求格式，参数值由具体实验预登记）：

```json
{
  "acceptance_schema_version": 1,
  "domain_ids": ["D01"],
  "required_rules": ["G-CORRECT", "G-COLLECT", "G-VALID", "G-COMPARE", "G-TARGET", "G-REGRESSION", "D01-PAIR"],
  "required_metrics": ["time.wall"],
  "optional_metrics": ["control.branch_mpki"],
  "policy_reference": "experiment-predeclared-policy",
  "correctness_reference": "experiment-oracle-contract"
}
```

## 4. D01 — 函数调用与控制流优化（P0）

**目标与问题：** 量化函数调用、虚调用、间接跳转、分支预测及冷热路径组织的成本，验证内联或布局调整是否降低端到端时间。
公共结果使用 `time.wall` / `time.iteration.wall` 和吞吐；其余指标如下。

| Metric ID（未注明已有者为设计） | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `cpu.branches`（已有） | D | 退休分支事件数，count | perf-stat branches；事件语义依 PMU |
| `cpu.branch_misses`（已有） | D | 分支误预测事件数，count | perf-stat branch-misses；必须与分支分母匹配 |
| `control.branch_mpki` | D | branch_misses / I × 1000，miss/1000 instructions | 同窗口硬件计数 |
| `control.branch_miss_rate` | D | branch_misses / branches，ratio | 同类分支集合；不能混入不同 privilege 范围 |
| `control.call_count` | D | Σ 被记录的调用入口次数，count | 函数插桩或完整 trace；内联后逻辑调用和机器 call 分开 |
| `control.hot_path_time` | D/O | 指定路径区间墙钟时长，s | region trace；明确 inclusive/exclusive，嵌套区间不重复累加 |
| `control.indirect_branch_miss_rate` | D | indirect_misses / indirect_branches，ratio | 对应 CPU 支持且语义一致的专用事件 |
| `cpu.hotspots`（已有结构化热点） | D | 符号 self sample period / 全部 sample period，% | perf-record；不是函数调用次数或精确时间 |

**条件、开销与误差：** 当前 perf 路径适用于 Linux/WSL；PMU 权限及虚拟化事件可信度必须单独探测。计数开销通常较低但短任务会受启动干扰；采样受频率、丢样、skid、符号质量影响；入口插桩可能改变内联和预测行为。

**解释与方向：** 误预测下降只能支持控制流假设，不能直接换算成固定周期损失。内联可能增加代码体积和 I-cache 压力；去虚拟化影响接口设计；branchless 可能增加无条件工作。用受控 A/B 保持输入分布，观察总时间和指令数。

**自动验收：** `D01-PAIR` 校验分支公式同样本分子分母；`D01-PATH` 要求路径耗时有真实区间证据，否则只能报告采样占比；`D01-OUTCOME` 禁止以 if/else 数量或 miss 下降替代 G-TARGET。

**Agent 流程：** 主 Skill → 端到端基线 → CPU 热点 → 热点源码与控制流 → 请求明确事件 → 受控内联/分支实验。与 D05/D06 联合归因，保存待验证假设。

**现有映射与缺口：** perf-stat 已映射 branches/branch-misses/instructions；perf-record 已输出热点。Branch MPKI/误预测率与统一目录已实现；Callgrind 动态调用计数及调用边已通过真实验证。精确原生路径时间和原生间接分支率仍待接入。

## 5. D02 — 算法时间与空间复杂度优化（P0）

**目标与问题：** 发现规模增长、重复计算、数据结构成本和候选算法交叉点；区分理论复杂度、有限范围经验趋势和端到端常数成本。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `algorithm.time_at_scale` | O | T(N) 或 T(x₁,…,xₖ)，s | 现有 study 引用普通 run；统一 ID 尚待注册 |
| `algorithm.doubling_ratio` | D | T(2N)/T(N)，ratio | 除 N 外维度固定的规模对；不插值冒充测量 |
| `algorithm.empirical_exponent` | D | ln(T₂/T₁)/ln(N₂/N₁)，dimensionless | 现有 local_slopes；要求不同正规模及正时间 |
| `algorithm.memory_at_scale` | O/D | M(N)，byte | 固定 heap 或 RSS 口径，不能混成一个曲线 |
| `algorithm.operations` | D | 已定义基本操作的执行次数，count | 显式目标计数/插桩；不能由时间反推精确次数 |
| `algorithm.variant_speedup` | O | T_baseline(N)/T_candidate(N)，ratio | 同一规模、同一输入、同一 oracle |
| `algorithm.model_prediction` | D | a·f(N)+b，s，a,b≥0 | 经验拟合为 Modeled；保存参数与适用范围 |

**模型与统计：** 复用 study 的 constant、log N、N、N log N、N²、N³ 候选和非负仿射拟合，报告留一交叉验证误差、残差和局部增长指数。至少四个不同规模才执行当前模型拟合；该条件不保证可靠识别复杂度。截距、cache 转折和启动成本可能使多个模型难区分。

多参数工作负载必须记录 N、nnz、稀疏度、活动比例等显式维度及关联约束。当前 study 只支持单轴变化；多参数拟合是后续扩展，需预定设计矩阵、避免共线性并保留外部验证点。不能将 nnz 与 N 的关联变化误称为只改变 N。

**条件、开销与误差：** 当前执行后端 Linux/WSL；原生重复计时和独立 oracle 可复用。规模增长实验耗时随负载增加；操作计数的插桩单独运行。随机输入固定 seed 和分布；cache、分配、热状态、噪声、规模范围及 I/O 都可能影响曲线。

**解释与方向：** 算法替换、索引、增量计算和复用可能降低工作量，也可能增加 setup、内存、失效维护及小输入成本。不能把最优拟合写成 Big-O 证明，也不能未经授权把交叉点硬编码到生产分派。

**自动验收：** `D02-DESIGN` 检查规模唯一、其他维度及分布固定；`D02-MODEL` 保存模型范围/误差且标为 Modeled；`D02-PER-SCALE` 每个预定规模均执行 G，重要规模回退单独否决，不只展示平均加速。

**Agent 流程：** 主 Skill → algorithms reference → 明确输入维度和 oracle → study → 检查模型残差与源码操作数 → 设计候选 → 逐规模比较。

**现有映射与缺口：** 单轴 study、增长拟合和逐规模比较已实现；已接入目标显式操作计数与多指标增长视图，并通过 DHAT 空间增长实验验证；多参数拟合仍待实施。

## 6. D03 — 计算代码与数值 Kernel 优化（P0）

**目标与问题：** 分析循环组织、SIMD、FMA、依赖链和执行资源限制，确定计算效率提升是否转化为 kernel 与应用收益。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `kernel.time` | O | 显式 kernel 区间时长，s | 注册 benchmark 或目标 region；不把整个进程改名为 kernel |
| `kernel.flops` | D | 实际执行标量等效浮点操作总数，FLOP | 可靠目标操作计数或受支持 PMU；记录精度、lane、FMA=2 约定 |
| `kernel.flop_rate` | O/E | FLOPs / T，FLOP/s | 同一区间；有用算法 FLOPs 与执行 FLOPs 分别定义 |
| `kernel.arithmetic_intensity` | D/E | FLOPs / B_level，FLOP/byte | 明确 L1/L2/LLC/DRAM 层级；操作数和流量覆盖相同工作 |
| `simd.vectorization_coverage` | D/E | 向量化执行的标量等效操作数 / 可向量化目标操作总数，ratio | 动态计数；编译器静态循环覆盖另命名，不能替代 |
| `kernel.instructions_per_operation` | D/E | I / Q，instructions/operation | 同区间退休指令与已定义操作数 |
| `kernel.roofline_efficiency` | E | (FLOPs/T) / min(P_peak, BW_peak·AI)，ratio | 峰值注明理论/实测、设备、精度、线程数、流量层级 |
| `kernel.resource_pressure` | D | 各执行资源每迭代需求，cycles/iteration | llvm-mca 等静态模型，Modeled，CPU 模型和输入汇编固定 |

**条件、开销与误差：** Google Benchmark 需要集成目标和优化消除屏障；FP PMU 必须逐架构核对 masked lanes、FMA 与事件覆盖。显式计数会扰动执行；静态分析不覆盖完整 cache/分支/OS 行为。不同精度、频率、线程数下的峰值不能混用。

Roofline 仅在 FLOPs、Bytes 和时间范围一致时生成。算法最低逻辑字节与实际 DRAM 字节不是同一数据；缺少后者时可建立注明假设的模型，不能称为硬件实测 Roofline。定义参考 [NERSC Roofline](https://docs.nersc.gov/tools/performance/roofline/)。

**解释与方向：** tiling/fusion/interchange、SIMD、FMA 和公共计算复用可能减少流量或指令；也可能增加寄存器压力、spill、代码量或尾部处理。浮点重排和 FMA 可能改变舍入，必须遵守既定数值 oracle，不自动启用 fast-math。

**自动验收：** `D03-COUNT` 保存操作定义及精度；`D03-ROOFLINE` 验证操作数/流量范围和峰值来源；`D03-NUMERIC` 强制数值约束；`D03-END-TO-END` 分别报告 kernel 与全流程时间，不能外推等比例加速。

**Agent 流程：** 主 Skill → 定位 kernel → 微基准与全流程基线 → 编译报告/计数确认向量化 → 同口径流量与吞吐 → 最小区分实验 → 正确性和端到端复测。

**现有映射与缺口：** Google Benchmark 已采集单 case 重复 wall/CPU 时间；perf 可提供受支持的指令/cycles。目标声明的有效算法 FLOP、逻辑输入字节及同重复吞吐已接入并实测；逻辑 AI 标为 Modeled。硬件 FLOP/流量、SIMD 覆盖、Roofline 和 llvm-mca 仍未实现。

## 7. D04 — 内存分配与内存管理优化（P0）

**目标与问题：** 量化分配频率、临时对象、生命周期、内存池、争用和碎片成本，区分峰值容量、累计分配量与分配耗时。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `memory.allocations_per_operation` | D | allocation_count / Q，allocations/operation | 分配追踪；明确 malloc/new/realloc、失败和零字节语义 |
| `memory.allocated_bytes_per_operation` | D | 累计成功请求字节 / Q，byte/operation | 累计流量，不是存活内存；realloc 采用明确计数政策 |
| `memory.rss_peak` | O | max RSS(t)，byte | 系统高水位或采样；注明是否包含子进程及共享页 |
| `memory.heap_peak_observed`（已有） | O/D | max 已记录快照的 mem_heap_B，bytes | Massif heap 快照；不包含全部 RSS，也不保证捕获瞬时峰值 |
| `memory.heap_peak_live` | O/D | max 同时存活的请求字节，byte | 完整分配/释放事件及覆盖；不同于快照峰值 |
| `memory.allocation_time_ratio` | D | 分配函数 exclusive CPU 时间 / 同范围总 CPU 时间，ratio | 插桩或归因计时；采样估计需标 Estimated |
| `memory.fragmentation_ratio` | D/E | allocator_reserved / allocator_live_requested − 1，ratio | 仅用于提供这两个同范围量的分配器，live>0；记录 allocator 定义 |

**条件、开销与误差：** 当前 Massif 路径要求 Valgrind 兼容的 Linux 目标；高开销会改变调度和生命周期交叠，不用其运行时间证明原生收益。Heaptrack 或分配器统计需要额外 Adapter。采样 RSS 可漏峰值，共享页归属、allocator metadata、缓存和未归还页会影响解释。
Massif 峰值具有快照选择限制，参见 [Massif manual](https://valgrind.org/docs/manual/ms-manual.html)。

**解释与方向：** pool/arena、reserve、对象复用和缩短生命周期可降低分配；可能增加常驻内存、延迟释放、跨线程争用或所有权复杂度。上述 fragmentation ratio 是明确约定的 reserved/live 开销，不自动区分内部、外部碎片；不得用 RSS/heap 代替。

**自动验收：** `D04-LIFETIME` 保持相同生命周期、负载、预热和清理边界；`D04-PEAK` 保留 heap/RSS/快照区别；`D04-ALLOCATOR` 比较分配器必须显式声明处理变量，不自动替换 runtime；`D04-RESOURCE` 同时检查原生时间和预定内存上限。

**Agent 流程：** 主 Skill → 原生时间 → Massif 观察峰值与分配栈 → 若问题是频率/争用，查询对应采集能力 → 分配策略 A/B → 原生性能与内存分别验收。

**现有映射与缺口：** Massif heap snapshot 峰值已实现并有既有实际验证；DHAT 已真实验证累计分配块数/字节、分配栈和同时存活峰值。RSS、原生分配耗时和碎片统计仍缺采集；DHAT 块数遵循其 realloc/allocator 语义，不等于所有 API 调用。

## 8. D05 — Cache、TLB 与数据布局优化（P0）

**目标与问题：** 分析局部性、pointer chasing、AoS/SoA/AoSoA、blocking、prefetch 和 cache-line 利用；区分容量、访问延迟与带宽限制。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `cache.llc_mpki` | D | LLC_misses / I × 1000，miss/1000 instructions | 明确 LLC 层级及 load/store/prefetch 集合的 PMU |
| `cache.l1_miss_rate`, `cache.l2_miss_rate`, `cache.llc_miss_rate` | D | 对应层级 misses / accesses，ratio | 同类访问事件；不能用其他层级引用作分母 |
| `cpu.tlb_misses` | D | 指定 iTLB/dTLB miss 事件数，count | 区分一级 miss、二级命中和 page walk |
| `cache.tlb_miss_rate` | D | 指定 TLB misses / 对应 translations，ratio | 只有可靠分母才计算；不可直接除以所有指令 |
| `memory.bandwidth` | D/E | B_level / T，byte/s | 内存控制器或缓存层级流量；明确读写和共享设备范围 |
| `memory.effective_bytes_per_operation` | D | 显式定义的有效数据字节 / Q，byte/operation | 逻辑字节与硬件传输字节分开记录 |
| `cache.line_utilization` | E/D | 使用的有效字节 / 传输的 cache-line 字节，ratio | 需要访问覆盖信息和一致窗口，否则仅模型 |
| `cache.stall_cycles` | D | PMU 定义的 cache miss 相关停顿事件，cycles | 记录事件定义；重叠停顿不能相加成耗时 |
| `memory.bandwidth_utilization` | E | 实测带宽 / 相同条件可持续带宽基准，ratio | 记录带宽基准的来源和读写比例 |

现有 `cpu.cache_references`、`cpu.cache_misses` 是 perf 通用事件原始计数。未确认具体 CPU 映射前，不别名为 L1/L2/LLC 指标，更不能由 misses×64 推断真实 DRAM 流量。

**条件、开销与误差：** PMU 与 uncore 访问依硬件和权限，uncore 往往是共享范围；系统后台负载影响归因。多路复用、预取、写回、cache 状态、页大小与 NUMA 放置影响统计。Cachegrind 是模拟来源，不能冒充实际 CPU 事件。

**解释与方向：** 连续布局、blocking、压缩和预取可能改善局部性，但可能增加转换成本、分支、工作量、内存和 cache 污染。延迟受限的 pointer chasing 不一定达到高带宽；miss 下降不等于省下 miss_count×固定延迟。

**自动验收：** `D05-LEVEL` 检查层级/事件集/计数范围；`D05-SOURCE` 禁止模拟与 PMU 数值混合比较；`D05-TRAFFIC` 带宽必须有实际字节来源；`D05-PIPELINE` 布局转换和临时内存计入全流程验收。

**Agent 流程：** 主 Skill → 原生布局 A/B → 热点和通用计数 → 查询特定层级及 TLB 能力 → 区分延迟/带宽/工作量假设 → 联合 D03/D06 验证。

**现有映射与缺口：** 通用 cache 计数已有 Adapter 映射，本机事件需 probe；Cachegrind 模拟已实现并标为 Modeled，通用硬件 cache 事件与独立原生布局时间已实测。LLC/TLB 专用事件、物理带宽和 LIKWID 仍未实现，不能由模拟值断言硬件根因。

## 9. D06 — CPU 微架构执行效率优化（P0）

**目标与问题：** 定位 front-end、back-end、错误推测、指令依赖及执行资源限制，解释工作量、cycles 和时间之间的差异。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `cpu.instructions`（已有） | D | 退休指令事件计数，count | perf instructions；记录包含线程及 privilege 范围 |
| `cpu.cycles`（已有） | D | 对应 PMU cycles 计数，count | 不默认等于 reference cycles；DVFS/SMT 必须记录 |
| `cpu.ipc`（已有推导） | E/D | I/C，instructions/cycle | 同样本、同范围、有效完整计数对 |
| `cpu.cpi` | E/D | C/I，cycles/instruction | 与 IPC 同条件；不能对平均 IPC 直接取倒数当平均 CPI |
| `cpu.frontend_bound` | D | 厂商版本化 TMA front-end slots / total slots，ratio | 匹配 CPU 的官方事件与公式 |
| `cpu.backend_bound` | D | 厂商版本化 TMA back-end slots / total slots，ratio | 相同 TMA 版本和测量范围 |
| `cpu.bad_speculation` | D | 厂商定义错误推测 slots / total slots，ratio | 不能由 branch misses 直接代替 |
| `cpu.retiring` | E/D | 厂商定义退休 slots / total slots，ratio | 不是 IPC，也不是有效算法工作比例 |
| `cpu.core_bound`, `cpu.memory_bound` | D | 对应 TMA 子类别贡献，ratio | 必须注明占 total slots 或父类别份额，禁止混用 |
| `cpu.port_pressure` | D | 指定执行端口事件或模型吞吐需求 | 实测事件和静态模型分开 ID/来源及单位 |

TMA 不是跨架构固定事件公式。本规范要求保存 CPU 型号、PMU、公式版本、总 slots 定义和分母口径，而不编造通用事件代数。层级分类及 slots 语义参考 [Intel TMA](https://www.intel.com/content/www/us/en/docs/vtune-profiler/cookbook/2024-2/top-down-microarchitecture-analysis-method.html)。

**条件、开销与误差：** 需要匹配 CPU 的事件和工具支持；虚拟化、SMT、频率和多路复用影响解释。WSL 事件可读不证明与物理机一致。静态资源预测只覆盖所选机器模型，不能当真实运行时间。

**解释与方向：** 减少依赖、提高 ILP、调整布局、降低工作量及优化访存均可改善性能，但 IPC 增加可能伴随更多指令或较低频率。不能跨 ISA/CPU 直接比较未经标准化的 IPC/TMA 并断言程序更优。停顿份额不是可无条件消除的时间。

**自动验收：** `D06-PAIR` 检查 IPC/CPI 原始计数同样本与覆盖；`D06-MODEL` TMA 公式匹配微架构；`D06-HIERARCHY` 父/子分母一致，不重复累加；`D06-OUTCOME` 同时呈现指令、cycles 与时间，不将 IPC 提升自动判为整体优化。

**Agent 流程：** 主 Skill → 热点 → instructions/cycles → 检查 PMU 有效性 → 有支持才请求 TMA → 与 D01/D03/D05 形成联合假设 → 受控实验验证。

**现有映射与缺口：** perf-stat 指令/cycles 及 summary 同样本 IPC 已实现；CPI 与核心 Metric Registry 已实现并验证；TMA、端口压力与完整架构公式仍待开发及硬件实测。

## 10. D07 — 多线程与并行效率优化（P0）

**目标与问题：** 量化线程扩展性、锁竞争、false sharing、同步、任务粒度和负载均衡，区分资源使用增加与有效工作增加。

| Metric ID（规范语义；实际可查询 ID 与范围见下方映射） | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `parallel.speedup` | O | T(1,N)/T(p,N)，ratio | 强扩展；同一问题和算法，p 为实际工作线程数 |
| `parallel.efficiency` | E | Speedup(p)/p，ratio | 单线程基线；若基线 p₀>1，则采用 [T(p₀)/T(p)]/(p/p₀) 并记录 p₀ |
| `parallel.weak_efficiency` | E | T(p₀,N₀)/T(p,N(p))，ratio | 弱扩展；每工作线程工作量及问题构造规则固定 |
| `parallel.lock_wait_ratio` | D | Σ线程锁等待时间 / Σ线程观测生命周期，ratio | trace 中明确锁请求到获取的区间；分开 spin 与 blocked |
| `parallel.load_imbalance` | D/E | max(Wᵢ)/mean(Wᵢ) − 1，ratio | Wᵢ 为各 worker 有效工作时间或操作数；单位和定义必须固定 |
| `parallel.synchronization_ratio` | D | Σ线程同步区间并集时长 / Σ线程观测生命周期，ratio | 锁、barrier、join 等已标记区间；嵌套等待去重 |
| `parallel.thread_utilization` | E/D | Σ线程 on-CPU 时间 / Σ线程可用窗口时长，ratio | 调度 trace 或可靠 CPU accounting；包含 spin，不能解释为有效工作比例 |
| `parallel.cpu_cost_per_operation` | O/E | 总 CPU 秒 / Q，CPU-s/operation | 同范围完整线程集合，结果正确性通过 |

**条件、开销与误差：** 原生线程规模计时可复用 Linux/WSL 执行后端；已支持应用显式工作/等待区间，以及 perf switch 事件的闭合 on/off-CPU 区间。后者不能识别锁等待或阻塞原因，详见 [thread-activity.md](thread-activity.md) 和 [scheduler-timeline.md](scheduler-timeline.md)。记录物理核/逻辑核、SMT、亲和性、嵌套线程池、实际线程数和超额订阅。插桩可改变争用；线程开始/结束与动态 worker 容量必须纳入分母，不能无条件使用 p×T。
强扩展固定全局问题，弱扩展固定每 worker 工作，不能把两者混成一条效率曲线；参见 [NERSC parallelism](https://docs.nersc.gov/performance/parallelism/)。

**解释与方向：** 分区、线程局部数据、减少共享写、任务粒度和同步结构调整可能改善时间，也可能增加复制、内存或尾部不均衡。超线性加速可能源于 cache 容量变化，需解释而非截断到效率 1。高 CPU 利用率不能排除忙等；低扩展性不能单凭总时间断言锁或 false sharing。

**自动验收：** `D07-SCALING` 校验强/弱扩展负载关系及 p₀；`D07-WORKERS` 校验实际线程和观测窗口；`D07-WAIT` 要求等待状态证据且禁止重复计数；`D07-RESOURCE` 每个预定线程数执行 G 并检查 CPU 成本与内存回退。只有缩放时间时可验收扩展收益，锁/false sharing 原因仍是未证实假设。

**Agent 流程：** 主 Skill → 固定语义和线程矩阵 → 原生时间/CPU 成本 → 检查扩展转折 → 查询锁/调度/一致性事件 → 区分同步、负载不均和内存瓶颈 → 逐线程数复测。

**现有映射与缺口：** 固定全局工作量的单机强扩展 Study 已实现并以合成负载验证；以声明的 worker 参数作为处理变量，具体范围和可比性见 [scientific-performance.md](scientific-performance.md)。请求的 worker 数不证明实际线程利用率。显式应用工作/等待区间和 perf 闭合切换区间可分别查询，但不自动推断锁等待原因；弱扩展、锁归因、false sharing 事件和完整 worker 追踪仍待实现。

## 11. D08 — 任务调度与 DAG 执行优化（P1）

**目标与问题：** 研究任务依赖、关键路径、调度开销、工作窃取和 ready queue 等待，区分可并行工作量不足与执行资源不足。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `dag.makespan` | O | 图提交边界至约定终端任务全部完成，s | 完整图实例 trace；是否包含建图在边界中声明 |
| `dag.total_work` | D | W=Σ cᵥ，s | cᵥ 为各任务 exclusive 有效执行时间，排除子任务及等待重复统计 |
| `dag.critical_path_time` | D | S=max路径 Σ cᵥ，s | 已知完整 DAG 和任务成本；记录成本为 CPU service 或无干扰参考时间 |
| `dag.available_parallelism` | E/D | W/S，ratio | 同图、同成本定义；理论并行潜力，不是实测线程利用率 |
| `dag.scheduling_overhead_ratio` | D | scheduler exclusive CPU 时间 / 同范围总 CPU 时间，ratio | runtime trace，包括明确分类的 enqueue/dequeue/steal；不混入任务执行 |
| `dag.task_wait_time` | D | task_start − task_ready，s | 每任务分布；依赖未满足时间不算 ready 等待 |
| `dag.task_throughput` | O | 完成任务数 / makespan，task/s | 任务定义和拆分粒度固定才可跨候选比较 |
| `dag.steal_success_rate` | D | 成功窃取次数 / 窃取尝试次数，ratio | runtime 显式事件；无尝试时不计算 |

**图与时间契约：** trace 至少提供 graph/task ID、依赖边、ready/start/end、worker、clock domain 和丢事件信息。图必须无环，依赖端点完整；动态任务与取消/失败语义明确。上述 S 是任务成本加权的依赖 span，不等同于包含 OS 抢占、队列等待的墙钟关键链。若要解释实际 makespan 的关键链，需另外记录资源串行边、等待因果和通信事件。
只有在固定成本、理想可调度资源模型下，max(W/p,S) 才作为理论下界；由实测成本构造的估计必须标注假设，不能声称精确可实现加速。

**条件、开销与误差：** 需要目标 runtime 插桩，可评估 Tracy/Perfetto/Caliper 等，但尚无 Adapter。跨线程时钟须可比；跨设备/节点须标定时钟和误差。短任务可能被事件开销主导；丢失依赖边、异步尾部和重复 task ID 会使图无效，不自动补边修复。

**解释与方向：** 合并小任务、减少依赖、提高数据局部性、工作窃取与队列策略可能降低调度成本，但会降低并行潜力、增加尾延迟或改变内存峰值。改变任务拆分后 task/s 无法单独表示收益，需比较同一业务工作量的 makespan。

**自动验收：** `D08-GRAPH` 校验依赖完整、无环及任务生命周期；`D08-CLOCK` 校验时间域和丢事件策略；`D08-SPAN` 区分成本 span 与墙钟关键链；`D08-OUTCOME` 对相同图语义及输出执行 G。无完整 trace 时关键路径指标为 Missing/Invalid，不从 CPU 热点推断依赖。

**Agent 流程：** 主 Skill → 图实例及完成条件 → makespan 基线 → runtime trace 能力 → work/span 与队列等待分解 → 粒度/依赖区分实验 → 端到端复测。

**现有映射与缺口：** 原生进程时间可作整体基线；无任务事件接入、图重建、关键路径算法或 DAG Adapter。未来追踪数据仍放进现有 evidence，派生分析引用 run，不新建 DAG 实验库。

## 12. D09 — 操作系统调度优化（P1）

**目标与问题：** 分析抢占、线程唤醒、调度延迟、迁移、上下文切换及系统调用成本，避免把阻塞或运行队列等待误认为 CPU 执行热点。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `os.runnable_latency` | D | 从进入 runnable 状态到下一次 running 的 Δt，s；报告 P50/P95/P99 | sched wakeup/switch 状态机；唤醒延迟和抢占后等待分组 |
| `os.context_switches`（已有） | D | 目标范围上下文切换计数，count | perf-stat context-switches |
| `os.context_switch_rate` | D | context_switches/T，count/s | 同目标/窗口；记录自愿/非自愿分类是否可得 |
| `os.cpu_migrations` | D | 定义的 CPU 迁移事件数，count | scheduler trace/软件计数；不从最后一次 CPU 位置推断 |
| `os.scheduling_delay_ratio` | D | Σ线程 runnable 未运行时间 / Σ线程观测生命周期，ratio | 线程状态完整；不含 sleeping/blocked |
| `os.kernel_time_ratio` | D | system CPU time / (user + system CPU time)，ratio | 同进程树和采集窗口的 accounting |
| `os.syscall_time` | D | 每次 syscall enter 到 exit 的墙钟间隔，s | syscall trace；可包含阻塞，不等于 kernel CPU 时间 |

**条件、开销与误差：** 本域内核事件路线针对 Linux；内核配置、tracepoint/perf 权限和容器 PID 命名空间需要探测。WSL/虚拟机中宿主调度不一定可见。追踪丢事件、线程 ID 重用、跨 CPU clock 与截断窗口影响延迟分布；需保留未配对事件比例，不把未配对区间补成零。
`perf sched timehist` 区分等待和调度延迟，参见 [Linux perf sched 文档](https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-sched.txt)。

**解释与方向：** 线程数量、亲和性、批处理和唤醒合并可能降低调度成本，但会影响公平性、响应时间、功耗和 NUMA 局部性。上下文切换多不直接证明性能损失大；系统 CPU 时间低也不能排除长 I/O 阻塞。不得自动修改优先级、系统调度器或管理员配置。

**自动验收：** `D09-STATE` 校验 runnable/running/blocked 状态及事件匹配；`D09-TAIL` 预登记分位数算法、样本量/覆盖与噪声门；`D09-SCOPE` 不以整机调度统计代替目标统计；`D09-OUTCOME` 保留端到端时间和响应尾部验收。权限不支持时 required trace 返回 UNSUPPORTED。

**Agent 流程：** 主 Skill → CPU 时间与墙钟差异 → 软件计数 → 判断需要 on/off-CPU trace → 按 runnable 与 blocked 分解 → 受控线程/亲和性实验 → 延迟及吞吐复测。

**现有映射与缺口：** context-switches 和 task-clock 已映射，需真实 probe；perf-record 已支持采样，并可选解析 `PERF_RECORD_SWITCH` 的双边界 on/off-CPU 区间，已在本机合成负载验证，见 [scheduler-timeline.md](scheduler-timeline.md)。它不是完整 perf sched 状态机，不包含 wakeup，不能据此计算 runnable 延迟或归因阻塞；迁移、内核时间和系统调用专项解析仍待实现。

## 13. D10 — NUMA 与虚拟内存优化（P1）

**目标与问题：** 量化 first-touch、线程/内存放置、远程访问、页错误、页大小和 TLB 成本；区分内存分配位置与实际访问流量。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `numa.remote_dram_ratio` | D/E | B_remote / (B_local+B_remote)，ratio | 同一请求方/窗口/读写集合的真实 DRAM 流量 |
| `numa.local_bandwidth`, `numa.remote_bandwidth` | D/E | B_local/T、B_remote/T，byte/s | 明确请求方、目标节点与共享控制器范围 |
| `os.page_faults`（已有） | D | perf page-faults 总数，count | 不自动拆为 minor/major |
| `vm.minor_faults`, `vm.major_faults` | D | 内核定义的相应 fault 计数，count | 进程 accounting/事件；major 不直接等于某一固定 I/O 延迟 |
| `numa.access_latency` | D | 选定本地/远程内存访问的延迟分布，ns | 标记地址与放置的微基准或 latency sampling；平均方式明确 |
| `cache.tlb_miss_rate`（复用 D05） | D | 指定 TLB misses / 对应 translations，ratio | 区分页大小、iTLB/dTLB 与 walk 集合 |
| `vm.resident_bytes_by_node` | D | 每节点驻留页数 × 对应页大小，byte | 页面放置快照，不是每节点访问字节 |

**条件、开销与误差：** Linux NUMA 拓扑、cpuset/memory policy、页大小、THP/hugepage 状态和自动 NUMA balancing 必须记录。多节点访问需要真实硬件或可说明的虚拟拓扑；单节点机器不能验证远程访问收益。uncore/厂商事件可能需要权限且覆盖整 socket；共享流量不可全部归因到目标。
页表/放置快照有采集成本并可能变化；fault 计数不证明具体耗时，采样延迟只代表被抽中的事件，不能冒充每次 load 的完整测量。

**解释与方向：** first-touch、绑定、分区复制和 huge pages 可能降低远程访问或 TLB 成本，也可能增加内存、迁移、碎片和负载失衡。`numastat`/页放置量不能直接用作 remote DRAM traffic ratio；缺少匹配流量时不计算比例。配置改动作为显式实验处理，不自动修改系统 hugepage 或 balancing 开关。

**自动验收：** `D10-TOPOLOGY` 保存实际节点/CPU/内存政策；`D10-TRAFFIC` 检查 local/remote 定义和同范围分母；`D10-PAGES` 固定页大小与初始化/预热条件，或声明为处理变量；`D10-OUTCOME` 检查原生时间、内存、fault 和初始化成本。单节点的远程指标为 UNSUPPORTED，不能填零后判通过。

**Agent 流程：** 主 Skill → 拓扑/页政策发现 → fault 与放置观测 → 有条件才测远程流量/延迟 → first-touch/亲和性区分实验 → 包含初始化的端到端验收。

**现有映射与缺口：** 环境发现记录基础 NUMA 信息，perf-stat 提供总 page-faults；没有节点流量、minor/major 分解、页面位置或 latency Adapter。当前环境的单节点信息只支持限制说明，不构成 NUMA 优化验证。

## 14. D11 — I/O 与数据序列化优化（P1）

**目标与问题：** 分析读写、编码解码、压缩、缓冲与系统调用成本；区分内存缓存路径、存储设备路径和持久化完成语义。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `io.effective_throughput` | O | 验证后的逻辑数据字节 / T，byte/s | 完整数据处理范围；压缩前/后字节定义固定 |
| `io.logical_read_bytes`, `io.logical_written_bytes` | D | 指定 API 成功传输的累计字节，byte | 目标计数/syscall trace；重复读写会重复计数 |
| `io.storage_read_bytes`, `io.storage_written_bytes` | D | 指定 storage accounting 的读/写字节，byte | 进程归因与设备计数分开；保留 OS 定义与 writeback 范围 |
| `io.serialization_time_ratio` | D | 序列化/反序列化 exclusive CPU 时间 / pipeline 总 CPU 时间，ratio | region 计时；不与异步墙钟阶段比例混用 |
| `io.wait_time` | D | 目标线程因指定 I/O 阻塞的区间并集，s | delay accounting/可归因 trace；不是机器级 iowait 百分比 |
| `io.operations_per_second` | O/D | 指定层级完成操作数 / T，operation/s | API 调用、block 请求和设备操作各有独立范围 |
| `io.end_to_end_time` | O | 输入准备边界至输出满足约定完成条件，s | 是否包含 flush/fsync、网络确认、解码明确 |
| `io.compression_ratio` | D/E | 原始逻辑字节 / 编码字节，ratio | 同内容和格式；仅是空间效率，不代表速度 |

**缓存与完成契约：** 实验声明 hot/cold/uncontrolled、准备步骤、输入身份、文件系统/挂载、存储设备、buffered/direct I/O、请求大小和并发度。没有证明冷缓存时标为 uncontrolled，不能擅自 drop caches。写入返回不等于数据持久化；是否计入 fsync 由预定需求决定。
Linux `/proc/PID/io` 区分字符计数和存储 accounting；不能把 rchar 当磁盘读取字节，也不能把内核归因字节称为硬件介质的精确流量，参见 [Linux proc 文档](https://kernel.org/doc/html/v6.10/filesystems/proc.html)。

**条件、开销与误差：** 当前外部程序可在 Linux 后端原生计时；进程 I/O/设备/trace Adapter 尚未实现。内核缓存、异步回写、设备缓存、压缩/去重、后台 I/O、短进程 accounting 及追踪开销影响解释。跨机器网络时钟与设备累计计数需独立范围，不以设备总量直接归因目标。

**解释与方向：** 批量 I/O、缓冲、压缩和编码布局可能减少系统调用或流量，但增加 CPU、临时内存、首条结果延迟和格式成本。优化必须保留数据等价、格式兼容和所需持久性，不能靠省略 flush 获得虚假收益。

**自动验收：** `D11-CACHE` 检查缓存与预热条件可比；`D11-BYTES` 检查逻辑/存储层级及压缩口径；`D11-COMPLETE` 检查输出完整性和完成语义；`D11-OUTCOME` 同时验收端到端时间、吞吐、CPU 和内存。缓存状态未控制时明确限制结论，不能通过固定阈值消除系统偏差。

**Agent 流程：** 主 Skill → 数据与完成契约 → 原生端到端基线 → 分离编码 CPU、等待和流量 → 明确缓存条件 → 压缩/缓冲/批处理 A/B → 内容 oracle 与持久性验收。

**现有映射与缺口：** Hyperfine 可量测完整程序时间，oracle 可验证输出；暂无 I/O 字节、syscall、delay 或序列化 region 采集。单次 process time 不得自动标为 serialization time。

## 15. D12 — 跨模块数据流与内存搬运优化（P0）

**目标与问题：** 识别重复复制、转换、遍历、临时表示与物化，量化计算外的数据组织成本；覆盖流水线而不只优化孤立 kernel。

| Metric ID | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `dataflow.copied_bytes_per_operation` | D | Σ显式复制请求字节 / Q，byte/operation | 同操作完整路径的 copy 计数；重复复制重复计数，不等于 DRAM 字节 |
| `dataflow.conversion_time_ratio` | D | 转换 exclusive CPU 时间 / pipeline CPU 时间，ratio | 已标记转换 region；不能与并发阶段墙钟简单相加 |
| `dataflow.full_traversals` | D | 对指定逻辑数据集完成的全遍历次数，count | 应用显式语义事件；数据版本和遍历集合固定 |
| `dataflow.temporary_peak_bytes` | O/D | maxₜ Σ存活的已标记临时对象字节，byte | 生命周期 trace/目标计数；不是各模块峰值之和 |
| `dataflow.movement_time` | D | 已标记搬运区间的耗时，s | 明确 CPU exclusive 或墙钟区间并集，两种统计不可混合 |
| `dataflow.pipeline_time` | O | 输入接收至最终验证输出完成，s | 包含转换、必要同步与异步完成；注明 setup/reuse 边界 |
| `dataflow.materialized_bytes` | D | 新建物化表示的累计请求字节，byte | 逻辑表示创建事件；区分累计和同时存活容量 |

**计数契约：** 定义模块/region、对象或 buffer ID、数据版本、操作 ID、复制方向、字节数和生命周期。memcpy/memmove 跟踪不覆盖所有内联复制或语义转换；被消除的 move 不一定搬运字节。部分、重复和分块遍历不能凭读取字节数自动折算成全遍历次数。

**条件、开销与误差：** 需要可审查的目标显式计数或 runtime 插桩；内联、异步、共享视图和零拷贝路径可能使函数拦截不完整。记录覆盖范围与时钟；跨线程重叠阶段的 CPU 总和可以超过墙钟，禁止据此生成超过 100% 的“墙钟占比”。本域方法可跨平台设计，当前执行后端仍限 Linux/WSL；新增采集必须真实验证。

**解释与方向：** 视图/借用、融合转换、延迟物化、缓存表示和减少中间层可能降低搬运，但增加生命周期耦合、别名风险、失效管理、驻留内存或随机访问成本。缓存复用必须保留失效语义；不得未经授权改变所有权契约。拷贝字节下降只证明逻辑工作减少，不能按内存带宽直接认定节省时间。

**自动验收：** `D12-COVERAGE` 标明可观测复制和转换覆盖；`D12-LIFETIME` 检查临时对象生命周期与峰值窗口；`D12-OVERLAP` 明确 exclusive/并集及异步完成；`D12-SEMANTICS` oracle 检查数据、布局转换和所有权相关约束；`D12-PIPELINE` 验收全流程时间及内存，不以孤立 copy benchmark 代替。

**Agent 流程：** 主 Skill → 绘制实际模块数据路径 → 全流程基线 → 标记转换与临时表示 → 区分重复工作与硬件流量 → 最小数据流变更实验 → 联合 D03/D04/D05 验收。

**现有映射与缺口：** 原生时间和 Massif 可提供整体时间/heap 线索。Google Benchmark 已支持目标显式提供的单阶段时间及复制字节、遍历、重建和复用等计数，见 [scientific-performance.md](scientific-performance.md)；普通程序也可通过显式阶段协议或[日志映射](phase-log-mapping.md)提供阶段及工作量。只有目标实际提供且范围清楚的计数才可解释；自动识别完整跨模块搬运、临时对象生命周期与归因仍未实现，逻辑复制字节不等于物理 DRAM 流量。

## 16. D14 — 数值精度、收敛性与求解效率优化（P1，纳入当前范围）

**目标与问题：** 在同一数值问题和已授权精度约束下，减少得到合格解的总时间与内存成本。研究迭代次数、预条件器、初始化、求解器选择及数值稳定性；不能通过放宽容差、改变离散问题或提前停止制造加速。

本域是通用数值分析契约，不绑定某一求解库。下面线性系统公式用于明确残差与误差语义；非线性问题必须声明自己的 F(x)、范数、缩放与接受条件，不能直接套用线性系统误差界。

| Metric ID（规范语义；实际可查询 ID 与范围见下方映射） | 类别 | 数学定义与单位 | 范围、来源与采集 |
|---|---|---|---|
| `solver.time_to_validated_solution` | O | 从预定求解流程开始至结果通过验证的墙钟时间，s | 包含约定的 setup、solve 和结果验证；失败时没有有效成功耗时，保留已消耗时间 |
| `solver.iterations_to_convergence` | D | 达到声明收敛条件时的迭代次数，count | 显式 solver 日志/计数；区分内外层迭代、restart 与 refinement |
| `numerical.residual` | D | 线性系统 r=b−Ax 的指定范数 ‖r‖，或非线性 ‖F(x)‖ | 单位依问题；记录范数、缩放、精度和计算位置，不默认无量纲 |
| `numerical.relative_residual` | D | ‖b−Ax‖/‖b‖，ratio | 本 ID 固定以 b 为归一化分母；以初始残差归一化需独立定义；b=0 时不可计算 |
| `numerical.absolute_error` | O/D | ‖x−x_ref‖，单位依解变量 | 独立可信参考解、指定范数及参考精度 |
| `numerical.relative_error` | O/D | ‖x−x_ref‖/‖x_ref‖，ratio | x_ref 非零；零参考按事先声明的绝对误差门验收，不补 epsilon |
| `numerical.normwise_backward_error` | D | ‖b−Ax‖/(‖A‖·‖x‖+‖b‖)，ratio | 线性系统、相容范数及明确的扰动模型；矩阵范数若估计须标 Estimated |
| `solver.setup_time` | O/D | 装配后约定 setup 起止区间墙钟时间，s | 明确排序、分解、预条件器构造是否包含；装配若不含须另记 |
| `solver.solve_time` | O | setup 完成至本次 solve 结束的墙钟时间，s | 包含迭代内部必要残差检查与同步；失败耗时仍留存 |
| `solver.total_time` | O | 声明的完整求解流程开始至结束，s | setup、solve、转换等以完整区间计时；重叠阶段不能简单求和 |
| `solver.peak_memory` | O | 观测窗口内所选 solver 内存口径的峰值，byte | 显式 solver 分配归因；进程 heap/RSS 仅可作为另名的整体代理指标 |

误差与残差是不同量；小残差不自动保证小前向误差，尤其在病态问题中。收敛停机判定和误差控制的区别参见 [Netlib Templates](https://www.netlib.org/templates/templates.html)。求解器报告的残差可能是预条件化量或递推量，不能直接标为重新计算的真实残差；例如 [PETSc KSP 文档](https://petsc.org/main/manual/ksp/)明确区分残差范数与收敛配置。引用用于定义语义，不代表接入或安装 PETSc。

### 16.1 问题身份、正确性与计时契约

- 固定问题输入、方程/算子、离散化、边界条件、右端项、数据类型、输出意义、目标误差及范数定义；保存输入哈希和参数。不同问题不能直接进行收益评分。
- 保存初值、预条件器、算法版本、停止规则、最大迭代数、线程配置和随机种子。优化方案允许改变明确声明的算法处理变量，但不得暗中改变最终精度要求。
- 精度/容差/停止条件调整需要明确授权及可审查的契约变更；不得自动启用 fast-math、混合精度、降精度或把发散转换成正常返回。
- 收敛原因是结构化结果：满足声明条件、最大迭代数耗尽、发散、breakdown、非有限输出、用户中止等保留原始原因。最大迭代数耗尽不是收敛；进程退出 0 也不等于合格解。
- 以独立验收计算或受信任的输出检查验证最终解。若没有参考解，可使用用户接受的残差/守恒/问题不变量等契约，但不能伪造前向误差；相应误差指标为 Missing，报告验证覆盖限制。
- 若本次目标要求收敛而未满足，正确性门 FAIL，不评分；日志或验证结果缺失为 INCONCLUSIVE。NaN/Inf 的输出不能参与正常误差比较。
- `time_to_validated_solution` 必须关联本次计时执行产生的结果及其验证，不能把一次预检查的通过状态附加到所有重复运行。验证计时包含与否由不同指标名区分，不篡改现有原生时间。
- setup 复用比较必须固定求解次数 K、右端项集合、缓存/预条件器失效政策。顺序无重叠时摊销成本可记为 (T_setup+ΣT_solve)/K；不能拿暖启动单次 solve 与冷启动完整流程直接比较。

### 16.2 条件、误差与分析方式

**条件、开销与误差：** 需要目标导出结构化迭代/收敛信息或可审查解析的日志，以及独立 oracle。当前平台 Linux/WSL 后端可运行外部程序，但没有通用 solver Adapter。每迭代残差重算、日志和高精度验证都有开销；固定监测频率，分别保存原生与插桩运行，不能混合其耗时。参考解也有误差，需要记录生成方法、精度和覆盖。

除最终误差外，保存迭代序号、累计时间、范数类型和对应残差序列；缺失的迭代点不插值冒充观测。收敛曲线可能非单调，不能通用地因某次残差上升判为发散。重复运行记录通过/失败的全部结果，不能只保留成功样本计算加速；数值稳定性要求由负载预先定义，不能用一次通过证明稳定。

**解释与方向：** 更好的预条件器、初值、复用、数据结构和求解算法可能减少迭代或单位迭代成本，也可能增加 setup、内存和维护成本。迭代数更少不一定总时间更短；不同算法的一次迭代工作不同。算法必须满足问题的数学前提，不能为追求速度对不满足条件的算子采用不适用方法。优化解释联合 D02/D03/D04/D07/D12，保留数值正确性硬门。

**自动验收：** 以下规则继承公共 G；阈值由问题契约提供，不设全局误差容限。

| Rule ID | 条件与判定 |
|---|---|
| `D14-PROBLEM` | 核对问题身份、精度/范数/最终接受条件与处理变量；问题或标准不一致则不可比较 |
| `D14-CONVERGENCE` | 收敛原因及独立验证满足合同；明确不收敛/错误输出为 FAIL，缺证据为 INCONCLUSIVE |
| `D14-NORM` | 区分真实/递推/预条件残差及归一化分母；不兼容的曲线不得合并，零分母不计算比例 |
| `D14-VALIDATED-RUN` | 合格解时间必须对应同次执行结果；仅有前置 oracle 不足以验收该指标 |
| `D14-REUSE` | 冷/暖启动、setup 复用次数和失效条件一致或作为预定处理；全部成本按约定边界纳入 |
| `D14-OUTCOME` | 通过数值门后，以 validated time/total time 和内存执行 G-TARGET/G-REGRESSION，不用迭代下降替代时间收益 |

**Agent 流程：** 主 Skill → 问题/精度/停止契约 → 独立 oracle 与 baseline → setup/solve/转换分解 → 收敛曲线与单位迭代成本 → 设计满足数学前提的方案 → 相同问题和精度下逐次验证 → 时间及内存比较。

**现有映射与缺口：** 结构化求解报告与逐次独立验证已接入非批量 Adapter，合成数值样例已验证终止原因、逐次残差/误差硬门和 setup/solve/total 等目标报告时间，见 [solver-validation.md](solver-validation.md)。普通前置检查不能替代逐次验证；目标报告的 `solver.total_time` 与原生 `time.wall` 均不包含外部独立验证，因此不能称为完整 Time-to-Validated-Solution。专用求解器日志自动识别、完整外部验证总耗时及 solver 专属峰值内存仍缺；Massif heap 是进程范围线索。构建产物由外部负责人提供，不接管编译流程。

## 17. 当前十三领域能力映射与实施顺序

下表按能力而非整域标记状态。已实现项的合成验证范围见
[domains-1-5.md](domains-1-5.md)、[scientific-performance.md](scientific-performance.md)、
[solver-validation.md](solver-validation.md) 和 [feedback-implementation.md](feedback-implementation.md)；
真实业务适用性仍需对应负载和工具探测。

| 领域 | 已实现或可复用的具体能力 | 仍需条件或尚未实现 |
|---|---|---|
| D01 | perf 计数/热点、同样本分支派生量、Callgrind 调用计数及边 | 精确原生路径时间、间接分支事件 |
| D02 | 单轴 study、经验拟合、增长比、显式操作计数和多指标增长 | 多参数设计与拟合 |
| D03 | Google Benchmark 时间与目标声明的有效 FLOP/逻辑字节，perf 指令/cycles | 硬件 FLOP/流量、Roofline、llvm-mca |
| D04 | Massif heap 快照、DHAT 分配块/字节及栈 | RSS、原生分配时间、分配器碎片和争用 |
| D05 | 通用 cache 事件及原生布局 A/B、Cachegrind 模拟 | 专用 TLB/LLC/uncore、物理带宽及 LIKWID |
| D06 | perf 指令/cycles、同样本 IPC/CPI 及目录 | TMA 和端口事件真实验证 |
| D07 | 线程配置、固定工作量强扩展 Study、应用工作/等待区间 | 弱扩展、锁归因、false sharing |
| D08 | 整体原生时间、evidence 存储 | 完整任务/依赖事件、DAG 重建和关键链 |
| D09 | context-switches/task-clock、perf 闭合 on/off-CPU 区间 | wakeup/runnable 延迟、阻塞原因及 syscall 解析 |
| D10 | 基础 NUMA 信息、总 page-faults | 多节点流量、延迟及页面位置采集 |
| D11 | 全流程计时、输出 oracle、显式阶段记录 | 物理 I/O accounting 和专用等待归因 |
| D12 | 全流程时间、目标显式阶段与工作计数 | 自动完整跨模块归因、临时对象生命周期 |
| D14 | 结构化求解报告、逐次独立验证与数值硬门、setup/solve/total | 专用日志自动适配、solver 内存归因、包含外部验证的完整耗时 |

核心 Registry、六态证据、同样本公式、单指标验收和 Skill 查询已接入既有系统；
算法、内存、CPU/Kernel 和原生 A/B 的合成实验已归档。后续应按真实负载
和工具条件选择证据，按需求推进 Suite 预登记汇总、弱扩展、DAG、
NUMA/物理 I/O 与完整 Time-to-Validated-Solution。只验证某类事件不能宣称整域完成；
不得为填满矩阵而新增不适用工具或修改全局配置。

### 17.1 自动化测试要求

以下为规范覆盖要求，不表示全部专项能力或对应测试已经实现；每项按实际能力及证据验收。

- 公式：IPC/CPI、Branch MPKI、误预测率、增长指数、收益和加权几何均值的已知输入；零分母、非有限值和非法权重应拒绝。
- 单位：seconds/ns、byte/KiB、ratio/% 正确换算；时间不能换成 CPU cycles，heap 不能换成 RSS。
- 兼容性：不同 sample、scope、method、CPU 事件定义、工具版本和计时区间不能自动合并。
- 状态：Unsupported/Missing/Invalid 不生成有效数值或 PASS；Modeled/Estimated 不冒充硬件实测。
- 验收：正确性失败阻止评分；噪声和区间跨界返回 INCONCLUSIVE；重要场景回退不能被 Suite 均值覆盖。
- 回归：旧 request/result、CLI、views、离线打包资源和现有 Python 测试保持兼容。
- D07：不同 p₀ 的强扩展公式、弱扩展负载约束、动态 worker 窗口、同步区间并集；不把线程数变化当普通相同条件比较。
- D08：已知 DAG 的 W/S、并行分叉和嵌套任务；环、缺边端点、缺失生命周期、时钟不一致应产生明确无效证据，不修图。
- D09：唤醒/抢占/阻塞事件序列、窗口截断和 PID/TID 重用；状态时长不可重复，尾分位数不能掩盖丢事件。
- D10：local/remote 同源配对、零流量分母、单节点 Unsupported；页面放置不能转换成访问流量。
- D11：逻辑/存储计数区分、缓存和持久化条件不匹配、异步回写范围；内容 oracle 失败不能评分。
- D12：重叠转换区间、同时存活临时峰值、部分遍历、重复复制、异步完成；模块峰值相加不能冒充全流程峰值。

- D14：覆盖零右端/零参考解、不同范数/预条件残差、非有限解、虚假退出成功、最大迭代耗尽、验证缺失、计时与结果不匹配、setup 复用不一致；已知答案的公式测试不能替代真实数值 oracle。

### 17.2 真实验收要求

| 实验 | 最少证据 | 可接受结论与边界 |
|---|---|---|
| 算法规模 | 多规模、重复样本、oracle、模型误差、每规模比较 | 有限范围经验增长，不证明渐近复杂度 |
| 内存 | 原始 Massif 快照、峰值和栈、相同生命周期 | 观察到的 heap 峰值，不替代 RSS/分配次数 |
| CPU/Kernel | 原始 perf 或注册 benchmark 输出、采集范围、工具限制 | 仅对实际采得指标结论；PMU 不可用时保留 Unsupported |
| 优化 A/B | 原生时间、正确性、固定负载、样本波动、回退检查 | 不确定结果允许 INCONCLUSIVE，不保证一定测得加速 |
| D07 强/弱扩展 | 实际线程矩阵、明确负载构造、每点重复和正确性 | 可报告扩展趋势；没有锁 trace 不解释精确同步成本 |
| D08 DAG | 可审查合成图、任务/依赖/worker trace、丢事件信息 | 完整证据才计算 span；不冒充实际墙钟关键链 |
| D09 调度 | 原始内核事件、状态重建、权限及覆盖记录 | CPU 采样不替代 runnable/blocked 证据 |
| D10 NUMA | 多节点拓扑、放置、配对流量及原生时间 | 单节点不验证远程收益；页错误可独立验证 |
| D11 I/O | 内容 oracle、缓存政策、完成条件、字节口径 | 未确认冷缓存时仅报告实际缓存条件结果 |
| D12 数据流 | 可审查转换/复制路径、阶段事件、临时生命周期 | 全流程包含转换与同步；不把逻辑字节当 DRAM 流量 |
| D14 数值求解 | 可审查独立数值样例、参考/残差 oracle、收敛原因、逐次输出、setup/solve/验证计时 | 相同问题和精度下比较；错误或不收敛候选不得评分。合成样例的逐次验证已有实测；完整包含外部验证的总耗时尚未实现 |

实验原始数据保存在现有 Store；报告引用真实 ID，不预填虚构结果。独立性能实验默认串行；显式并发 A/B 按项目 AGENTS 记录竞争条件并保持区块串行，其他 CPU 密集检查不与测量重叠。不得为本规范安装不适用工具或改动业务工程。

## 18. Agent 使用及后续领域

当前主入口为 [cpp-performance Skill](../skills/cpp-performance/SKILL.md)，通过根 AGENTS 显式加载。
本轮不重命名 Skill、不声称 UI 注册，不新增尚不可执行的 CLI 示例。
现有 `doctor`、`tools list/probe`、`metrics`、`plan`、`run`、`analyze`、`compare` 和 `study` 继续使用。Skill 已接入领域/指标查询；项目入口加载和实际 CLI 验证见 [core-metrics.md](core-metrics.md)。

Agent 应按以下证据链工作：真实工作负载与正确性 → 原生基线 → 热点及资源分布 → 选择相互关联领域 → 查询并探测能力 → 低干扰采集 → 瓶颈假设 → 区分根因实验 → 方案 → 正确性/时间/资源验收。
对 pointer chasing 同时出现的 cache miss、低 SIMD 和 backend bound，形成一个有证据的因果候选，不能机械输出多个独立优化结论。
瓶颈成本优先报告受控实验中的时间差；没有可辨识因果实验时不生成精确“损失百分比”。

其余领域按实际需求调整如下；保留 ID 便于后续引用，不构成实施承诺，也不计入已交付领域数量。

| ID | 名称 | 当前决策 | 范围说明 |
|---|---|---|---|
| D13 | 编译器、链接器与二进制布局 | 暂缓 | 编译由其他人员负责；不开展编译优化，仍保存已有构建元数据和二进制身份用于可比性检查 |
| D14 | 数值精度、收敛性与求解效率 | P1，已纳入 | 数值契约与结构化报告的逐次验证已接入；继续按负载扩展诊断及完整验证耗时 |
| D15 | GPU 与异构计算 | 当前范围外 | 当前没有需求，不规划 Adapter 或工具安装 |
| D16 | 分布式计算与 MPI | 当前范围外 | 当前没有需求，不规划 Adapter 或运行环境 |
| D17 | 能耗、功率与热效率 | 暂缓，当前不考虑 | 不建设能耗采集、功率/热专项分析或能效验收，不作为当前交付缺口 |

公共收益契约中 EnergyChange 仅保留为未来可选语义，当前不采集、不评分、不设能耗验收门。
当前工作集中于 D01–D12、D14 的协议、实现与验证；不以完整覆盖 17 个领域为由扩大范围。

## 19. 本版本交付边界

本版本完成 D01–D12、D14 共十三个领域的规格、共同证据契约、目标验收规则与现有架构映射。
核心 Registry、六态证据、IPC/CPI/分支派生量、规模增长比、单指标验收及结构化逐次求解验证已实现，真实合成实验已执行；完整领域采集、Suite 和包含外部验证的完整求解总耗时仍待实施。以 [core-metrics.md](core-metrics.md) 和 [solver-validation.md](solver-validation.md) 的接口与限制为准，本文其余“设计”字段不自动成为可调用功能。
后续更新必须同时注明规范版本、语义变更、实现状态与实测证据，不能以表格行数代表平台能力。

2026-10-15 实现状态补记：D07 已接入固定工作量强扩展 Study，D12 已接入 Google Benchmark 显式单阶段秒数与复制/遍历/重建/复用计数。真实合成负载已验证；D14 已接入显式结构化求解日志、独立逐次残差/误差重算及硬门，见 [solver-validation.md](solver-validation.md)。弱扩展、锁等待归因、自动阶段重建、专用求解器日志适配及完整外部验证总耗时仍未实现。接口、误差与复现方式见 [scientific-performance.md](scientific-performance.md)。本补记不改变 v0.3.0 领域规格语义。

### 19.1 版本记录

- 0.1.0：完成 D01–D06 与公共指标/证据/验收契约。
- 0.2.0：补齐 D07–D12，扩展能力矩阵、自动化测试及真实验收要求；新增领域均为规格设计，不宣称新增 Adapter 或实测完成。
- 0.2.1：按实际职责和需求暂缓 D13/D17，D15/D16 移出当前范围，D14 保留待讨论；能耗不作为当前交付或验收要求。
- 0.3.0：按用户决定将 D14 纳入 P1，补齐数值指标、问题/精度契约、逐次验证与测试要求；其他暂缓领域不变。

### 19.2 D01–D05 实施状态更新

现已通过独立合成样例验证 Callgrind/DHAT/Cachegrind 与 Kernel 显式计数接入，以及同一 study 的多指标时间/空间增长。可运行命令、精确范围与剩余缺口见 [domains-1-5.md](domains-1-5.md)。该实施更新不将高级领域规格自动标为已完成。
