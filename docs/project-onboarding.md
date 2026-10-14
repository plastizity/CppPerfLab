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

复用目标工程已经验证的构建方式，在独立目录保留 baseline 和 candidate。
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

```bash
bash scripts/install.sh
source scripts/env.sh
perflab doctor --json
perflab tools list --json
perflab schema request --json
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
