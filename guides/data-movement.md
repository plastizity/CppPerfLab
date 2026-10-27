# 数据搬运诊断与有效工作量

本接口把完整调用的结果、使用方定义的工作量和缓存事件并列，帮助提出和
区分性能假设。它不测量物理传输字节，也不自动判定内存瓶颈。使用方负责
定义有效工作、输出正确性和等价任务；平台保存原始证据并检查计数的范围。

## 先探测事件组

选择一个与任务相符的性能目标和可信 oracle，确认执行预算，再探测本机所需
事件及同调度能力：

```bash
perflab tools probe --tool perf-stat --events instructions,cache-references,cache-misses --event-group instructions,cache-references,cache-misses --json
```

`--event-group` 可重复；每个组至少两个不同的硬件事件，均须包含在 `--events`
中，组不能重叠。结果必须检查可用性及失败原因；平铺事件可用不证明组合可以
同调度。机器、虚拟化、权限及 CPU 事件定义都会影响结果。不可用时保留原因，
不使用零值或模拟数据替代。

本仓库此前在当前 WSL2 环境的分组探测记录
`20261027T033046-090747fee9cd` 成功；事件的 `:u` 用户态过滤保留在原始证据。
更早的探测 `20261027T032735-e75ee759ba17` 因解析未预期的 `:u` 后缀失败，
该失败记录仍保留。探测只证明当时的探测条件，不保证其他机器或后续运行可用。

## 配置工作量报告

以下是现有 request 的 `measurement` 片段，不是可直接运行的完整 request：

```json
{
  "tool": "perf-stat",
  "options": {
    "events": ["instructions", "cache-references", "cache-misses"],
    "event_groups": [["instructions", "cache-references", "cache-misses"]]
  },
  "work_report": {
    "schema_version": 1,
    "path": "invocation.work.json",
    "definition_id": "sum-items-v1"
  }
}
```

`work_report` 只支持非批量 `perf-stat`；其 `path` 是测量目录内的
`*.work.json` 文件名。每次目标调用应写入自己的报告，例如在目标支持相应
参数时传入 `{run_dir}/invocation.work.json`。文件由目标在该次调用期间写出，
其开销也包含在目标测量范围内。报告格式示例：

```json
{
  "schema_version": 1,
  "definition_id": "sum-items-v1",
  "unit": "item",
  "count": 1000,
  "scope": "target-invocation",
  "count_kind": "observed",
  "completed": true
}
```

`definition_id` 要与配置一致，`unit` 是稳定的非空工作单位，`count` 是非负
整数，且应只计本次已完成的有效工作。`work.items` 的 Measured 表示目标自报
数值，不证明其业务定义、完整性或跨版本等价；这些由使用方和 oracle 验证。
跨运行比较前需对齐定义、单位、负载、正确性和其他既有可比性条件。

可执行文件、Build ID、显式构建 artifacts、环境和原始文件仍由既有 Run 机制
归档。关键动态库应列入 artifacts；需要核实实际加载对象时，显式配置
[runtime_identity](runtime-identity.md)。本轮九组示例未启用实际库映射观察，
不能据此证明每次测量加载了完全相同的动态库；后加一次观察也不能补造历史证据。

## 解释诊断

| 指标 | 计算 | 解释及前提 |
|---|---|---|
| `cpu.cache_miss_ratio` | cache misses / cache references | 同事件组、同运行时间/范围及有效覆盖率至少 99%；通用事件含义依 CPU 而异，结果可大于 1，不能当作概率。 |
| `cpu.cache_mpki` | cache misses / instructions × 1000 | 同组、同范围及有效覆盖率至少 99%；这是每千退休指令的通用缓存事件比，不是物理字节或特定缓存层缺失。 |
| `cpu.instructions_per_work` | instructions / work.items | 同调用同范围；硬件事件必须来自有效组且报告覆盖率 100%。 |
| `cpu.cache_misses_per_work` | cache misses / work.items | 同调用同范围；硬件事件必须来自有效组且报告覆盖率 100%。 |

覆盖率由 perf 报告，可能经过舍入，并非绝对精度保证。四项派生值均为
Estimated；分母为零时为 Missing。保留来源及状态；不得跨 Run 拼接计数，也
不得用阶段工作量除以全进程事件。缓存 Miss × 64 不能代表物理流量；Uncore
和内存带宽采集不属于此接口。诊断数值变化并不证明其造成了运行时间变化。

现有 Comparison 的收益验收要求 Measured 输入，上述 Estimated 比值不获得通过
评分。符合可比性和正确性条件的原始缓存计数或自报工作量可使用既有配对及
联合评估；结果、正确性、工作变化与资源回退仍需分别判断，不生成综合单分数。

## 查询与报告

对已完成的 Run，使用实际返回的 ID：

```bash
perflab analyze RUN_ID --section data_movement --json
perflab report RUN_ID --format both --output results/data-movement --json
```

视图按结果、工作量、数据搬运三栏展示观测、范围和证据；人类 HTML 与 Agent
报告复用该视图。也可对 Study、Comparison 或 Summary 指定相应 `--kind`。
查看原始 perf 文件和工作量报告，结合时间与正确性设计区分不同原因的实验，
不能将并列结果直接写成因果结论。

## 独立合成示例与执行预算

示例只构建本仓库的小型 `data_movement` 目标，使用独立 oracle 验证精确整数
计算；不会编译使用方工程。生成器只写配置，不编译、不探测、不运行目标。

```bash
cmake --preset profile
cmake --build build/profile --target data_movement
python3.11 scripts/make_dme_examples.py --blocks 3 --sizes 32768 262144 --rounds 8 --output workloads/dme
perflab budget --kind study --config workloads/dme/pointer-32768-native.study.json --json
perflab study --config workloads/dme/pointer-32768-native.study.json --json
perflab analyze STUDY_ID --kind study --section data_movement --json
perflab report STUDY_ID --kind study --format both --output results/dme-example --json
```

最后两条使用实际返回的 Study ID。先查预算，再单独执行所需 Study；不要并行
运行无关的重负载。默认配置生成 9 个 Study：两类访问/布局场景 × 两个规模 ×
原生时间和计数两个 profile，加一个计数 A/A。每项 Study 为 3 个 ABBA 区块、
12 个 Run，全部执行共 108 个正式样本；不含预热（warmups=0）。每个 Run 另有
一次 oracle、一次目标预检及工具探测；每次 oracle 内部执行 49 次短目标调用
（一次 `--verify` 和 48 个案例），因此全套还包含 5,292 次 oracle 内部调用，
以及 108 次预检和对应探测。平台预算展示外层次数，不能推断 oracle 内部成本。
配置中的阈值和三组区块只用于示范，须在正式实验前按场景和预算另行制定。
保存每个 Study 的配置、Run、原始计数和报告；分组探测成功不等于实验的统计
结论已经成立。

本仓库已完成一轮独立合成目标验证：9 个 Study、108 个正式样本，验证记录
`.perflab/validations/dme-20261027/verification.json` 的工作流状态为 PASS；各
Study、Run 和逐指标结果见同目录 `studies.json`，报告位于
`results/dme-20261027/`。例如，32768 规模 A/A 缓存计数 Study
`20261027T033327-4c1114e8d2e4` 为 `no_clear_change`；相同规模的布局原生
时间 Study `20261027T033258-08e13e92f223` 为 `no_clear_change`，计数 Study
`20261027T033305-8b9abc68149f` 的缓存缺失分类为 `observed_regression`。
这两项布局比较的对应验收均为 INCONCLUSIVE，不能等同于确定的端到端回退或
优化成功。每组仅三个区块，展示接口而非确立通用统计门；WSL2 guest PMU 的
证据也不构成原生 Linux 硬件资格认证。

联合评估可用 `diagnostic` 角色将原始缓存事件作为信息项，具体限制见
[联合评估指南](joint-assessment.md)。
