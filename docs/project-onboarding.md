# 接入一个 C++ 工程

适用对象：使用者和负责接入的 Agent。当前平台支持 Linux/WSL2 上的已编译
可执行程序，以及已集成 Google Benchmark 的程序。接入普通程序不需要链接
CppPerfLab，也不要求修改业务源码。本指南描述已实现接口；构建编排、调优器、
自动源码修改和安全沙箱尚未实现。

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
  当前没有自动清缓存或重置输入的 hook；有状态程序必须先定义并验证复测语义。
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
错误分类见 [协议](protocol.md#lifecycle-and-errors)。

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
构建步骤见 [本地依赖](downloads.md)，查询和展示见 [统一分析视图](analysis-views.md)。
多规模与多个预构建算法候选使用 `study`，见
[算法实验指引](../skills/cpp-performance/references/algorithms.md)。经验拟合不是 Big-O 证明。

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
映射见 [phase-log-mapping.md](phase-log-mapping.md)。该命令不执行目标程序。
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
- 需要新旧版本收益时，预登记 [paired Study](paired-studies.md)，直接执行该
  Study，不把“各跑一次再跑配对”当作强制流程。资源竞争不是研究目标时使用
  串行交错；显式并发 A/B 的结果按 [并发条件](concurrent-paired.md)解释。
- 需要并列全过程、阶段、实际工作量和资源门时，使用
  [joint assessment](joint-assessment.md)；其当前接口要求归档 cases 和串行条件，
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
