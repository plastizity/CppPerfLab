# CppPerfLab 使用说明

适用对象：使用者和负责接入的 Agent。当前平台支持 Linux/WSL2 上的已编译
可执行程序，以及已集成 Google Benchmark 的程序。接入普通程序不需要链接
CppPerfLab，也不要求修改业务源码。本指南描述已实现接口；构建编排、调优器、
自动源码修改和安全沙箱尚未实现。

## 平台与使用方的约定

接入前先确定：**使用方定义什么是有效任务、允许哪些变化、什么结果值得接受；
平台负责按明确范围测量执行成本、保存证据并执行预先约定的验收规则。平台的
通过结论只覆盖已声明且已执行的检查。** 以下责任约定不等同于自动验证：
使用方可选择配置平台支持的 Claim、执行状态动作及分层 Study 来源，具体字段和
检查范围见[实验有效性指南](guides/experiment-validity.md)。声明本身并非证据，
平台也不能自动证明因果关系、任意状态等价或测量独立性。

“使用方”包括需求负责人、项目维护者和算法维护者；可以由同一人或 Agent
承担多个角色，但责任不能因使用平台而转移。

| 事项 | 需求、项目或算法侧维护 | 平台负责 |
|---|---|---|
| 有效任务与收益 | 需求侧确定要完成的任务、代表性场景、主要目标、不可接受的回退及资源预算 | 记录目标和实验配置，执行受支持的预设验收门；不代设业务收益或统一综合分数 |
| 问题与等价性 | 算法侧定义输入/输出含义、精度及停止要求、算法允许变化，以及两个实现为何可比较 | 执行既有正确性命令并检查配置/证据可比性；匹配哈希或 oracle 退出零不证明问题等价或验证充分 |
| 负载与工作量 | 项目侧提供输入、规模、分布及版本；算法侧定义一次操作、迭代、复制等计数的含义与实现 | 保存身份，校验格式、单位、范围和来源；不把同名计数自动当作等量工作，也不猜缺失工作量 |
| 构建与目标接入 | 项目侧提供已构建程序、相关库/插件及构建记录，维护函数/类 Adapter、解码器与验证器 | 运行受信命令，记录声明身份，按可用能力观察实际加载；不自动修改、编译或选择业务实现 |
| 执行状态 | 项目侧定义初始化、冷态、预热、复用、重置及状态恢复的含义，并提供相应实现/证明 | 执行已支持的预热或生命周期，记录条件及可观察证据；不把新进程视为冷缓存，不以 reset 返回成功证明状态等价 |
| 阶段与领域信息 | 项目侧定义阶段边界、父子关系、日志映射、输入分类及领域计数 | 维护通用协议、解析和层级统计；不识别未声明的业务阶段，不从热点自动推出领域根因 |
| 测量与统计 | 使用方预先选择目标范围、负载、预算和验收策略，并说明必要环境控制 | 探测能力，按既有 Run/Study 执行，保留样本、误差、六态证据、可比性和统计结论 |
| 解释与采纳 | 算法/项目侧解释机制，设计区分原因的实验；需求负责人决定是否接受工程代价、是否部署 | 分开报告事实、假设、变化与验收；可建议验证实验，不以统计 PASS 自动批准优化 |

### 平台考虑的性能范围

平台可以测量不同层级的通用执行成本，**测量边界必须由本次目标明确选择**：

| 测量对象 | 应明确的成本范围 | 不可替代的结论 |
|---|---|---|
| 完整程序/任务 | 进程时间、实际工作量，以及可采集的 CPU、内存等资源；初始化、I/O、准备和输出是否位于该边界内 | 函数或热点比例不能直接代替完整任务收益 |
| 阶段/Kernel | 指定阶段的包含/自身时间、调用数和对应工作量；未知覆盖或重叠保持缺失 | 不重复相加父子时间，不由局部变化推出全过程变化 |
| 独立函数/类 | 按已选生命周期分别记录加载、准备、重置、调用、验证与销毁 | `invoke` 时间不包含其外部准备；进程硬件/堆指标不改名为精确函数指标 |
| 实验工作流 | 平台编排、探测、预检、预热、外部验证、解析、归档及报告的运行次数、预算和可取得的成本证据 | 编排耗时不作为目标程序性能；没有计时证据时不估造各步耗时 |

同一项准备、转换或验证成本是否属于优化目标，取决于声明的范围，不能一律
忽略或一律计入。现有外部前置 oracle、解析和报告不在工具目标计时内；目标
进程内部执行的准备/校验则仍属于其进程时间。完整外部独立验证耗时尚未形成
通用 Time-to-Validated-Solution 指标，不能仅凭目标计时使用这个名称。

平台对自身引入的干扰负责说明和控制；采样、插桩、进度写入或身份观察的开销
不能默认为零，也不自动从实测时间扣除。工具可用不等于所有指标都必须采集。

### 每次实验最少对齐的内容

使用方的接入记录和现有实验配置应共同说明：

1. **对象与范围**：完整任务、阶段或函数；准备、I/O、验证等成本如何处理。
2. **负载与固定条件**：输入/规模、版本、线程及其他必要环境；哪些变量允许改变。
3. **有效完成条件**：可信验证命令、覆盖范围及既定约束；由项目/算法侧维护。
4. **工作与状态**：有效工作如何计数；初始状态、复用/重置的含义及已能验证的部分。
5. **目标与预算**：主要结果指标、资源/回退门、重复及预热成本；阈值在比较前确定。
6. **主张与适用范围**：本次只是测量、比较还是验证某个原因；结论适用于哪些输入和状态。

不要求首次接入就提供全部阶段、工作计数或硬件事件。只有全过程时间和可信
oracle 时，也可完成该范围的测量与比较；缺少工作/状态证据时，不能升级为
“单位计算效率提高”“冷启动改善”或普遍适用的优化结论。已声明为必要的约束
缺失或失败时，应保留失败/不确定/不支持结论，不能通过删去该项获得通过。

工作量减少可以是合法的算法优化。例如复用准备结果或避免重复搜索，只要仍
完成约定任务，就不应因操作数变化而自动否决；报告应将其与单次执行效率改善
区分。任务内容、完成程度或既定约束改变，则必须重新说明比较对象和允许的主张，
不能继续沿用原结论。

即使已记录计数相同，也只能说明这些计数相同，不能证明所有实际工作一致。

可选 Claim、执行状态动作和探索/确认来源记录见[实验有效性指南](guides/experiment-validity.md)。
状态动作与消费者验证各有范围，当前不提供通用计算状态快照恢复；新的 Run ID
和冻结记录也不证明统计独立或对新输入泛化。项目声明、平台观察和无法验证的
部分需要分别说明。

## 安装与快速入口

在 CppPerfLab 根目录执行。需要 Python 3.11+ 和至少一种可用测量工具；安装是
离线、项目本地的 zipapp，不安装系统依赖或修改全局配置。

```bash
bash scripts/install.sh
source scripts/env.sh
perflab --version
perflab doctor --json
perflab tools list --json
perflab schema request --json
```

每个新 shell 重新执行 `source scripts/env.sh`；平台代码更新后重新执行安装脚本。
具体工具路径可通过项目本地配置提供，说明见[工具配置及报告](guides/analysis-views.md)。
工具安装或注册不代表本机有采集权限，应检查工具探测结果与目标兼容性。

## 常用实验流程

先根据下方的接入说明建立实际 JSON 配置；以下 `CONFIG`、`STUDY_CONFIG` 和
`RUN_ID`/`STUDY_ID` 是占位符，必须替换。业务实验配置、输入、日志、Store 和
导出报告保存在平台仓库外。`budget` 不执行目标，`plan` 的集成目标探测可能执行
Google Benchmark 注册列表查询；`run`、`study` 和预检会实际运行目标及正确性命令。

```bash
perflab budget --config CONFIG --store /absolute/consumer-lab/store --json
perflab plan --config CONFIG --store /absolute/consumer-lab/store --json
perflab run --config CONFIG --store /absolute/consumer-lab/store --json
perflab analyze RUN_ID --store /absolute/consumer-lab/store --json
perflab budget --kind study --config STUDY_CONFIG --store /absolute/consumer-lab/store --json
perflab study --config STUDY_CONFIG --store /absolute/consumer-lab/store --json
```

单 Run 或 Study 应依据目标选择一种执行路径；需要比较时可预先配置[配对实验](guides/paired-studies.md)，
不用为了接入默认增加完整的单独运行。正式执行前检查预算、正确性命令、预检、
预热、重复次数和实际工具能力。平台只运行已审查且已构建的目标。

### 导出两份报告

```bash
perflab report STUDY_ID --kind study --format both --output /absolute/consumer-lab/report --store /absolute/consumer-lab/store --json
perflab report RUN_ID --format agent --store /absolute/consumer-lab/store --json
```

第一条命令生成 `report.html` 和 `report.agent.json`；两者使用同一次分析视图。
第二条返回精简 Agent 摘要。单 Run 导出时省略 `--kind study`；`--kind` 还支持
`comparison` 和 `summary`。原始结果、证据和所有样本留在 Store，详细查询及 `--limit`
行为见[报告与分析视图](guides/analysis-views.md)。执行完成、验收通过和加速证据
是独立结论；缺失或不支持的指标不视为零或通过。

### 测量函数或类

已审查的 C++17 小型适配程序可以通过 [case.hpp](include/cppperflab/case.hpp) 接入函数
或类，使用方负责输入解码、目标调用和独立验证。具体 hooks、输入文件格式、
每次创建/复用重置的生命周期及复现命令见[函数与类测量框架](guides/file-case-framework.md)；
昂贵或失败的输入可按[样本保存与回放](guides/sample-capture.md)使用既有 Store 和比较机制。
函数调用阶段有单独计时；进程范围的硬件计数和内存指标不会自动变成函数区域计数。

## 专题指南索引

按需求阅读，不必全部加载：

| 主题 | 相关指南 |
|---|---|
| 环境、指标和报告 | [本地依赖](guides/downloads.md)、[D01–D05 分析](guides/domains-1-5.md)、[多指标总结](guides/performance-summary.md)、[报告与查询](guides/analysis-views.md) |
| 阶段与日志 | [阶段观测及预算](guides/phase-observation.md)、[格式/关键字映射](guides/phase-log-mapping.md) |
| 对照与联合验收 | [实验有效性与分层确认](guides/experiment-validity.md)、[串行配对](guides/paired-studies.md)、[并发配对](guides/concurrent-paired.md)、[联合评估](guides/joint-assessment.md) |
| 输入与独立测量 | [样本回放](guides/sample-capture.md)、[函数/类框架](guides/file-case-framework.md) |
| 科学计算 | [工作量与强扩展](guides/scientific-performance.md)、[数值求解验证](guides/solver-validation.md) |
| 数据搬运与工作量 | [数据搬运诊断与内存控制器能力预检](guides/data-movement.md) |
| 运行与诊断 | [运行状态](guides/run-status.md)、[实际加载身份](guides/runtime-identity.md)、[CPU 采样](guides/sampling-interpretability.md)、[应用线程活动](guides/thread-activity.md)、[OS 切换时间线](guides/scheduler-timeline.md) |

正式指标语义见 [性能领域与指标规范](docs/performance_domain_metrics_spec.md)，
数据契约见[协议](docs/protocol.md)。下面是已有程序的完整接入及验收说明。

实现职责和能力边界见[架构](docs/architecture.md)、[核心指标](docs/core-metrics.md)
及[工具选型](docs/tool-selection.md)。[历史验收](docs/history/acceptance.md)保留各轮
测试与实验记录；[反馈实施记录](docs/history/feedback-implementation.md)还包含已有
功能的限制与后续缺口，移入历史目录不表示这些缺口已经完成。

## 1. 接入前确认的信息

Agent 先读取目标工程的 README、AGENTS.md、CMake Presets、测试和运行脚本。
能从工程获取的信息自行调查，不要求使用者重复提供。

| 信息 | 从哪里获取；何时需要使用者补充 |
|---|---|
| 工程路径、构建目标、编译器、依赖 | 工程配置与构建记录；多个候选入口无法确定时再确认 |
| 代表性负载、输入文件、规模、分布和随机种子 | 测试/案例/生产运行方式；没有真实数据时由使用者提供或指定替代负载 |
| 优化目标 | 明确是端到端耗时、kernel、吞吐量、内存还是扩展性；不能由热点百分比代替 |
| 正确性标准 | 测试与算法契约；缺失的数值容差、收敛条件、拓扑语义必须先确认，不能自定宽松阈值 |
| 线程、动态库、运行环境、许可服务 | 运行脚本；记录必要配置，不把密码或令牌写入实验 JSON |
| 操作范围 | 区分只测量、创建独立构建、修改候选源码；遵守用户授权和目标仓库规则 |

建议把这些信息及未解决项放入该工程的接入记录，与配置一起维护。
CppPerfLab 当前仓库规则不授权修改业务仓库；本指南不会自动放宽该边界。

## 2. 先准备构建与正确性命令

优先复用目标工程已经验证的 baseline 和 candidate 产物及构建记录；已有可用
产物时不要求重新编译。确需构建时，由获得授权的工程负责人按原构建方式提供。
平台当前运行预构建程序，`target.build` 只是元数据，不会执行 CMake。
性能构建保持既定浮点语义；需要采样时保留符号/合适的栈展开信息。
Sanitizer 构建单独验证，不与普通性能构建比较耗时。

正确性命令必须实际检查当前候选，非零退出即拒绝测量。可以使用：

```bash
ctest --test-dir /absolute/project/build-profile --output-on-failure --no-tests=error
```

先确认项目的 CTest 支持该参数且确有相关测试。测试全通过只证明其覆盖范围；
对科学计算还需相应的误差、收敛、边界和退化输入检查。不能用 `/bin/true`
代替 oracle。跨候选比较时，oracle 应执行等价的验证。

## 3. 建立外部程序实验配置

下面是普通程序模板。所有 `/absolute/...` 均需替换，程序参数必须按目标的
真实 CLI 修改；不要直接运行占位模板。不使用环境变量插值或 shell 拼接。
JSON 是当前支持的配置格式。

```json
{
  "schema_version": 1,
  "name": "project-baseline",
  "workload": {
    "id": "project-representative-workload",
    "parameters": {"threads": 1},
    "inputs": ["/absolute/data/input.dat"]
  },
  "target": {
    "argv": ["/absolute/project/build-profile/app", "--input", "/absolute/data/input.dat"],
    "environment": {},
    "build": {
      "revision": "unknown",
      "compiler": "unknown",
      "flags": [],
      "mode": "RelWithDebInfo",
      "artifacts": ["/absolute/project/build-profile/compile_commands.json"]
    }
  },
  "measurement": {
    "tool": "hyperfine",
    "repetitions": 9,
    "warmups": 2,
    "timeout_seconds": 300,
    "options": {}
  },
  "correctness": {
    "argv": ["/absolute/toolchain/bin/ctest", "--test-dir", "/absolute/project/build-profile", "--output-on-failure", "--no-tests=error"],
    "description": "Replace with the actual correctness checks and their coverage"
  }
}
```

将真实版本、编译器和参数填入 build；未知项诚实标记 unknown。
`threads` 只是描述信息，不会设置线程数：通过目标实际支持的 argv 或
`target.environment` 显式控制，并保证两者一致。列出 oracle 脚本、关键动态库、
构建记录等相关 artifacts；平台只对声明的文件取哈希，不自动保存源码或数据副本。

运行行为中几个容易误解的点：

- executable、inputs 和 artifacts 的相对路径按 JSON 所在目录解释；argv 中其他路径
  原样传递，推荐绝对路径。`$HOME`、`~`、`*` 不会自动展开。
- 默认 cwd 是每个执行阶段的独立目录；`target.cwd` 可显式设置，但共享目录需要
  自行控制输出覆盖和状态污染。正确性命令也使用该 cwd 和 environment。
- `{run_dir}` 可用于 argv 输出路径，替换为当前执行阶段目录；不是每次内部
  repetition 的新目录。Hyperfine/Google Benchmark 一批重复运行可能共享文件状态。
  可选的 `execution_state` 支持配置受信任的状态动作和验证器，但批量工具不能
  在每次内部 repetition 之间执行样本级动作。平台不自动清理全局缓存或恢复任意
  快照；有状态程序必须由使用方定义并验证复测语义。
- 子进程仅继承最小 PATH/locale/TZ 和显式 environment；动态库路径、线程设置等
  必须明确提供。stdin 关闭，不支持交互式输入；使用受支持的文件/参数入口。
- 计划探测也会启动程序：Google Benchmark 会执行注册列表查询。仅接入可信程序。
  超时/地址空间限制和工作目录隔离不构成不可信代码的安全沙箱。
- timeout 对批量计时是整批限制，包含预热及重复；应按真实负载预算设置。

## 4. 调用和验收

在 CppPerfLab 根目录安装并加载环境；新终端重新 source 即可，不修改全局配置。
先将下方 Store 占位路径替换为平台仓库外的实际实验目录。该环境变量仅影响
当前 shell 的后续命令；也可逐条使用 `--store` 指定相同目录。

```bash
bash scripts/install.sh
source scripts/env.sh
export CPPPERFLAB_STORE=/absolute/consumer-lab/store
perflab doctor --json
perflab tools list --json
perflab schema request --json
perflab budget --config /absolute/config/baseline.json --json
perflab plan --config /absolute/config/baseline.json --json
perflab run --config /absolute/config/baseline.json --json
perflab report RUN_ID --format json
perflab analyze RUN_ID --json
```

doctor/probe 的退出码 0 只代表发现完成；要检查各工具 available/reason。
plan 的 selected/candidates 给出实际可用方案，不能把“命令存在”当成可采集。
正确性失败或工具失败时保留原始证据，修复明确原因后启动新实验，不修改旧结果。
错误分类见 [协议](docs/protocol.md#lifecycle-and-errors)。

**接入成功**：目标身份和输入正确；oracle 实际执行；run completed；存在真实
样本、工具原始文件和环境记录；report 可重读；测试失败能阻止计时。
首次检查一次 raw 文件，核对单位和计时范围。接入成功不等于已找出瓶颈。

后续 candidate 配置保持 workload、输入、线程、工具和测量条件相同，替换
可执行文件、相应构建身份及 oracle 指向，再执行：

```bash
perflab run --config /absolute/config/candidate.json --json
perflab compare BASELINE_ID CANDIDATE_ID --metric time.wall --json
perflab baseline PROJECT_CASE_BASELINE BASELINE_ID --json
```

ID 使用真实返回值。baseline 命名是保存引用，不会替使用者批准优化。
不要为了绕过不可比检查而使用 `--allow-command-change`；仅在明确解释参数差异后使用。
编译器/编译参数差异目前记录为 treatment，仍需人工/Agent 审阅，不能认为 compare
成功就排除了这些混杂因素。`inconclusive` 表示证据不足，保留所有样本，不挑最快一轮。

**收益通过**：除接入成功外，还需正确性覆盖充分、测量条件可比、结果稳定、
交错 A/B 复核，以及多个规模/分布没有不可接受的回退。平台不会自动合并代码。
微基准加速不能直接作为端到端收益。

## 5. 专项分析和交付

工具能力及选项以 `tools list`、`schema`、`plan` 为准，避免维护另一份工具清单。
Google Benchmark 需要在目标内注册 case，当前 filter 必须恰好选中一个 case；
使用 `time.iteration.wall` 或 `time.iteration.cpu` 比较。
构建步骤见 [本地依赖](guides/downloads.md)，查询和展示见 [统一分析视图](guides/analysis-views.md)。
多规模与多个预构建算法候选使用 `study`，见
[算法实验指引](skills/cpp-performance/references/algorithms.md)。经验拟合不是 Big-O 证明。

交付给下一位使用者/Agent：接入记录、构建和验证命令、实验 JSON、输入/源码
版本获取方式、run/study ID、原始证据目录、结论及限制。配置可能含绝对路径；
换机器需重新解析配置并探测能力。只复制结果摘要无法复现；原始输入和构建产物
由工程自身保存，实验目录应随报告保留。分享前检查记录的路径、环境及日志内容。

## 6. 生产阶段报告的接入验收

本节使用现有配置、Run/Study、Comparison 和报告，不增加接入专用执行器或
验收协议。实际业务配置、路径、日志、输入和 Store 由使用方保存在平台仓库外；
平台内的公开示例继续使用合成负载。平台本身的合成验证不能替代使用方接入验收。

### 先交接可执行条件

| 内容 | 使用方需确定的条件 |
|---|---|
| 执行范围 | 由谁运行、允许执行哪些既有二进制/检查命令、是否只解析已有日志；不隐含授权修改源码或重新构建 |
| 输入与状态 | 代表性输入及版本、工作量定义、线程设置、输出位置和重复执行的状态语义 |
| 正确性 | 检查当前目标的可信命令、数值精度/收敛约束及覆盖范围；缺失时不批准收益 |
| 阶段接入 | 格式模板或关键字映射，或程序导出的通用阶段记录；明确单位、包含关系、调用次数和工作量字段 |
| 比较目标 | 预先确定时间范围、资源门、样本/波动要求、收益与回退门槛；不照搬合成示例阈值 |
| 交接位置 | 实验配置、映射、外部 Store、Run/Study ID 和报告位置；敏感数据不复制进平台仓库 |

已有阶段日志可先用 `perflab phases import` 离线检查映射；格式模板和关键字
映射见 [phase-log-mapping.md](guides/phase-log-mapping.md)。该命令不执行目标程序。
一次解析成功只说明这份日志符合配置，不能证明程序正确、完整阶段覆盖或性能收益。
源日志只有汇总时间时不补造起止区间、自身时间、调用数或未覆盖时间。

### 昂贵程序只选一条正式执行路径

先查看 `budget` 中正确性、预检、预热、正式测量和逐次验证的次数；它不会执行
目标程序，也不承诺这些步骤的预计秒数。随后按需 `plan`；能力探测可能执行
Google Benchmark 列表查询。预检会实际运行目标，不是免费的静态检查。
使用方应在执行前评估这些成本；不为接入验收自动追加一次完整运行。

- 已有可追溯的完整 Run/Study 时，先查询原记录及证据，判断能否满足本次验收；
  原身份或覆盖不足时记录缺口，不修改旧结果或把缺失补成通过。
- 首次接入单版本时，使用普通 `run` 检查程序、阶段和正确性链。一次正式样本
  可以验证接口，不能支持稳定加速结论。
- 需要新旧版本收益时，预登记 [paired Study](guides/paired-studies.md)，直接执行该
  Study，不把“各跑一次再跑配对”当作强制流程。资源竞争不是研究目标时使用
  串行交错；显式并发 A/B 的结果按 [并发条件](guides/concurrent-paired.md)解释。
- 需要并列全过程、阶段、实际工作量和资源门时，使用
  [joint assessment](guides/joint-assessment.md)；其当前接口要求归档 cases 和串行条件，
  不承诺任意生产程序配置都能直接套用。只有所需证据齐备时才启用相应门。

以下命令只查询已产生的记录；将 ID 和仓库外路径替换为实际值：

```bash
perflab status RUN_ID --store /absolute/consumer-lab/store --json
perflab analyze RUN_ID --section phase_summaries --store /absolute/consumer-lab/store --json
perflab report RUN_ID --format html --output /absolute/consumer-lab/run.html --store /absolute/consumer-lab/store --json
perflab analyze STUDY_ID --kind study --section comparisons --store /absolute/consumer-lab/store --json
perflab report STUDY_ID --kind study --format html --output /absolute/consumer-lab/study.html --store /absolute/consumer-lab/store --json
```

`phase_summaries` 用于汇总日志映射；带起止区间的阶段记录使用 `--section phases`。
按已有证据类型选择，不要求同一程序同时导出两种格式。

### 三种结论分别交付

| 验收 | 可据以判断的证据 | 不足时如何报告 |
|---|---|---|
| 日志接入 | 原始日志、映射、转换结果与来源行；单位及父子关系经人工核对 | 只报告已解析范围；未声明的调用数、时间线和工作量保持缺失 |
| 程序接入 | 真实 Run ID、目标/输入身份、独立正确性结果、正式样本和原始工具证据；启用加载身份观察时还需审阅其覆盖与门 | 尚无实际程序运行时标记未验证；解析成功不能替代本项 |
| 优化收益 | 所有预定负载的可比结果、正确性与统计门、全过程加速证据、工作量变化及资源代价 | `PASS` 与“无明确加速”可以同时成立；保留 FAIL/INCONCLUSIVE/UNSUPPORTED 及其原因 |

给平台或下一位 Agent 的验收摘要只需引用：配置/映射位置、Run/Study ID、
报告位置、三种结论、证据限制及下一步实验。不把业务日志复制到平台提交中，
也不以局部阶段下降替代全过程收益。
