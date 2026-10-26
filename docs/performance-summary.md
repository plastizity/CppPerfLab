# 多指标分析、总结与汇报

同一份总结供 Agent JSON 查询和离线 HTML 使用，不计算综合优化分数，也不将
“指令工作量降低”转换为“实测加速”。各维度保留独立测量来源和结论。

## 汇报维度

| 维度 | 内容 | 判断边界 |
|---|---|---|
| 计算工作量 | 动态指令、计算/访问/迭代计数 | 不受 SIMD、缓存或耗时结论支配 |
| 内存与资源 | 堆/RSS 峰值、分配、逻辑搬运 | 峰值、累计量与次数分别保留 |
| 执行行为 | cycles、缓存/分支事件、缺页及切换 | 观察事件变化，不自动认定根因 |
| 指令执行质量 | IPC、SIMD 有效利用及向量化证据 | SIMD 出现/占比不等于有效利用或更快 |
| 耗时 | 进程/迭代的 wall 与 CPU time | 只比较语义一致的计时范围 |
| 正确性与数值质量 | oracle 结果、误差、残差 | 通过 oracle 不自动生成数值误差曲线 |

这些是汇报字段，不表示对应采集器均已实现。缺失项显示 not_collected/partial，
不填零。未知指标保留在未分类维度，不推断优化方向。当前 Adapter 能提供的
指标以 tools list/probe 为准；自定义算法计数、SIMD lane 利用率和数值误差的
自动采集尚未接入。

IPC 仅从同一重复、同一统计范围且覆盖充分的 instructions/cycles 硬件计数
配对推导，保留原始值和证据引用。不使用不同 run 的中位数构造 IPC；分母为零、
数据缺失、范围不符或计数运行覆盖不足时不推导。IPC 不是 SIMD 利用率。

## 单次实验与单项比较

```bash
perflab analyze RUN_ID --section summary --json
perflab compare BASELINE_ID CANDIDATE_ID --metric cpu.instructions --json
perflab analyze COMPARISON_ID --kind comparison --section summary --json
```

指令变化显示 observed_work_reduction/increase，耗时显示 observed_time_reduction/
increase；执行质量指标按数值变化展示，保持进一步解释的责任。噪声大的计时
可以 inconclusive，同时稳定的指令变化仍可以形成独立结论。
已有 compare 的 speedup 字段对非耗时指标只是历史命名的中位数比；综合报告
使用 baseline_candidate_ratio，不称其为速度提升。

## 汇集多份记录

```bash
perflab summarize --run RUN_ID --run OTHER_RUN_ID --comparison COMPARISON_ID --json
perflab summarize --comparison INSTRUCTION_COMPARISON_ID \
  --comparison TIME_COMPARISON_ID --format html --output /tmp/perflab-summary.html --json
perflab analyze SUMMARY_ID --kind summary --section summary --json
perflab report SUMMARY_ID --kind summary --format html --output /tmp/perflab-summary.html --json
perflab schema summary --json
```

比较记录会引用并带入对应 baseline/candidate run。summary 记录仅保存已有
run/comparison ID，不复制样本、不新建实验数据库，也不执行目标程序。
不同工具、输入或范围的数据保持独立观察；汇集不代表它们可直接比较。
比较结论复用原有 compare，引入总结不会放宽正确性或统计政策。
失败记录可汇报，但保留错误和退出码；仍在运行的记录不用于保存最终总结。

需要在运行前声明完整输入、两个版本、各 profiler 及联合验收门时，使用
[联合性能评估](joint-assessment.md)。它复用 paired/case Study 与 Comparison，
区分全流程加速、局部改善、工作量和内存代价，并校验跨 profiler 的声明文件身份。

报告显示维度覆盖、独立结论、所有原始指标、运行条件、正确性范围、待验证
假设和证据。多次采样百分比不相加，性能收益不自动批准。

## 独立示例复现

```bash
python3.11 scripts/make_examples.py
bash scripts/install.sh
source scripts/env.sh
perflab run --config workloads/pair-counters-baseline.json --json
perflab run --config workloads/pair-counters-candidate.json --json
perflab study --config workloads/instruction-study.json --json
```

使用返回的 ID 构造上述比较及总结。指令实验需要 perf 硬件事件实际可用。
增长报告输出 a·g(x)+b 的表达式、系数、截距、误差、测量点和范围；候选函数是
常数、log x、x、x log x、x²、x³，不是任意多项式回归或数学 Big-O 证明。

## 研究方法与扩展界限

指令质量可以借鉴 [Roofline](https://amcr.lbl.gov/departments/computer-science-department/ppan/roofline-performance-model/)、
[Top-Down](https://www.intel.com/content/www/us/en/docs/vtune-profiler/cookbook/2025-4/top-down-microarchitecture-analysis-method.html)
和 [llvm-mca](https://llvm.org/docs/CommandGuide/llvm-mca.html) 的方法，但它们需要
不同的计数、硬件模型和适用条件。当前总结没有生成 Roofline/Top-Down 模型。

近期值得后续评估的数值方向包括 [Chassis（ASPLOS 2025）](https://arxiv.org/abs/2410.14025)
的目标相关误差—性能权衡、[ExplaniFloat（ARITH 2025）](https://arxiv.org/abs/2503.11884)
的误差诊断，以及 [Accurate Residues（2026 预印本）](https://arxiv.org/abs/2604.06258)
的残差计算。平台尚未接入这些工具；论文效果不作为平台效果或通用保证。
代码集成仍需固定来源、核对许可证、正确性要求、开销和目标语言接口。
