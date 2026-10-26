# 原生串行交错对照与配对区块统计

Study 的 `analysis: "paired"` 扩展现有运行矩阵，不另建执行器或结果数据库。
每个成员通过 `lab.run` 执行，Comparison 负责所有条件检查、统计、成本和验收。
原 growth/strong_scaling、非配对 compare 和旧结果记录保持原义。

## 预算先行与复现

```bash
# 仅显式构建本仓库合成目标；不构建使用方工程。
cmake --build build/profile --target phase_text
python3.11 scripts/make_phase_example.py
python3.11 scripts/make_paired_examples.py
bash scripts/install.sh
source scripts/env.sh
perflab schema study --json
perflab budget --kind study --config workloads/paired-text.study.json --json
perflab study --config workloads/paired-text.study.json --json
perflab analyze STUDY_ID --kind study --section all --json
perflab report STUDY_ID --kind study --format html --output results/paired.html --json
```

预算预览不执行 probe、oracle、目标或构建。当前 `budget --kind study` 支持 paired
和文件 case 设计，普通请求继续使用默认 `--kind request`。每个版本的 `run_calls` 乘以其
`each_run` 内各阶段计数，即完整成功路径成本；失败会提前停止。
每个成员仍执行自身探测、oracle、预检及配置的预热，不隐式复用正确性结果。
校验脚本的内部调用和自适应 benchmark 迭代仍标记未知，不能猜测总耗时。

## 配置

以下结构的数字属于可编辑合成示例，门槛必须在比较前声明：

```json
{
  "schema_version": 1,
  "analysis": "paired",
  "name": "serial-control",
  "baseline": "baseline.request.json",
  "candidate": "candidate.request.json",
  "blocks": 7,
  "order": "abba",
  "seed": 20261026,
  "budget": 28,
  "metric": "time.wall",
  "policy": {
    "schema_version": 1,
    "min_samples": 7,
    "max_cv": 0.1,
    "max_regression_fraction": 0.05
  },
  "constraints": "Declare identical numerical task, inputs and work definition",
  "resource_conditions": {
    "concurrent_runs": 1,
    "background_load": "uncontrolled",
    "notes": "No isolation enforced"
  }
}
```

- `random_ab_ba`：按预设 seed 为每个区块选择 AB 或 BA。
- `abba`：每个区块执行 ABBA；seed 仍保存但固定顺序不使用随机数。
- A/B 分别指 baseline/candidate。每个请求必须显式选择同一工具，并且
  repetitions=1。预热次数仍由请求声明，不能把一批独立结果按行号伪配对。
- `budget` 是最大 `lab.run` 调用数，至少覆盖整个矩阵；不是强制花完的预算。
- `metric_scope` 可精确选择一个阶段路径。`secondary_metrics` 可列多个
  `{metric, metric_scope?, policy}`，每项必须显式给出政策，不能把时间目标
  无条件套到内存或诊断指标上。
- 除已明确允许的命令参数处理变量，声明的输入/参数/工具/控制须匹配。
  可比性声明检查在执行前拒绝明显不一致；原始测量的环境、事件、scope、
  覆盖率、证据和正确性仍通过同一 Comparison 检查。

`resource_conditions` 是调用方声明。平台保证本 Study 的启动顺序串行，但不保证
没有第三方负载、强制独占 CPU 或消除温度/缓存漂移。同步竞争双跑目前不支持，
concurrent_runs 不是 1 时拒绝，不把竞争场景暗中当作串行独立实验。

## 来源、停止与版本固定

Study 执行前保存区块计划和两个版本的输入/构建指纹。每个成员有 block_id、slot、
variant、原始 run_id、开始/结束时间和状态。下一次启动前及完成后均检查声明的
身份，版本或输入变化则停止。哈希不是安全隔离或源代码正确性的证明。

任何成员、数值门、证据或比较条件失败都会保存原始运行及已完成的区块，然后
停止。没有隐式重跑、区块删除或以剩余成功区块重新评分。取消传播为 cancelled。
只有所有配置的比较均成功计算后才归档这些比较结果；统计验收可以是 FAIL 或
INCONCLUSIVE，这与工具/工作流 completed 分开。

每个比较拥有真实 comparison_id 并引用 study_id。`baseline_id`/`candidate_id`
仅是首个来源上下文，**全部来源**由 baseline_run_ids/candidate_run_ids 和
block_pairs 标识。独立比较报告和汇总会读取全部来源，而非只显示首对证据。

## 统一统计口径

AB/BA 各有一个 A 和 B；ABBA 各取两个 A、两个 B 的中位数，形成一对区块代表值。
代表值是从实测推导的 Estimated 值，原始成员仍保留 Measured 状态与证据。

点估计为：

`median(block baseline representatives) / median(block candidate representatives)`

Bootstrap 使用现有 5000 次、固定统计 seed=1014 的引擎，成对抽取**区块索引**，
同时保留对应 A/B 值；设计顺序 seed 与统计 seed 分别保存。ABBA 的四次运行
不是两个独立配对，统计样本量按区块计数。区间始终是描述性统计，不证明区块
独立、因果关系或完全消除环境漂移。

边际 CV 继续报告，帮助观察环境变化；paired 稳定性门使用每区块 A/B 比值的 CV。
政策中的 min_samples 作用于区块数，max_cv 的 basis 显式记录在 policy 中。
这允许共同漂移下的稳定比值被识别，同时不把较大的边际噪声隐藏起来。
正确性、回退门、预设目标、加速证据和归一化成本都复用原 Comparison 机制。
多个指标分开报告，不能把不同单位相加评分；诊断项没有明确方向时不自动评分。

此合成示例两边是同一个程序，只验证顺序、来源、统计和负向控制，不能当作真实
优化。生产程序仍需代表性输入、充分数值约束、跨规模检验及全过程/资源验收。
