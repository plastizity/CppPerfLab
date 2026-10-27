# 工具选型与能力边界

实际支持的能力、选项、许可证和可用性以 `perflab tools list`、`tools probe`
及带目标的 `plan` 为准。这里解释选择依据，不维护第二份能力注册表。

## 典型工具组合

下表解释部分工具的选型，不是完整 Adapter 清单。Callgrind、DHAT 和 Cachegrind
等后续接入能力见 [D01–D05](../guides/domains-1-5.md)；完整列表仍查询 CLI。

| 工具 | 选择依据 | 主要边界 |
|---|---|---|
| [Hyperfine](https://github.com/sharkdp/hyperfine) | 低侵入的重复进程计时，原生 JSON 保留所有样本 | 包含进程启动；不是 kernel 微基准 |
| [Linux perf](https://perf.wiki.kernel.org/) | 同时提供事件计数、CPU 采样和原始调用栈 | 依赖实际事件权限、硬件与符号能力；采样占比不是耗时 |
| [Valgrind Massif](https://valgrind.org/docs/manual/ms-manual.html) | 观察堆快照及分配栈，不要求目标源码插桩 | 运行干扰明显；快照峰值不是 RSS、分配次数或原生耗时 |
| [Google Benchmark](https://github.com/google/benchmark) | 集成式 C++ 微基准，有重复迭代计时和原生 JSON | 需要目标注册 benchmark；当前只适配单 case 的 wall/CPU 计时 |
| 项目内 native-resource | Linux `wait4` 采集单次目标调用的 RSS 高水位、CPU 时间及缺页数，不依赖硬件计数器 | 含启动与已等待后代的资源语义；RSS 不是进程树同时总量，不提供原生耗时；需显式构建本地 helper，见[原生进程资源](../guides/native-resources.md) |

上述组合互补；不将不同计时范围、内存指标或采样结果强行合并。
安装某个工具不代表它能在本机采集。显式配置不可用时返回诊断，不隐式切换。
依赖配置及构建步骤见 [本地依赖](../guides/downloads.md)。

## 算法分析的实现方式

规模实验和预构建候选比较复用现有 run、Measurement 与存储。模型拟合是
有限范围的经验分析，不证明渐近复杂度，也不自动选择生产实现。
Agent 负责理解源码和提出候选；平台负责运行已审查候选、检查正确性及保留证据。

后续参数搜索可以评估 [pyATF](https://github.com/atf-tuner/pyATF)，但目前没有
该 Adapter。引入前必须固定版本、核对许可证、定义参数约束和评估预算，继续
复用现有实验存储。有限实验矩阵不能描述为已完成自动调优。

代码演化框架暂未接入：生成候选需要独立编译、可信 oracle、成本预算及真正的
执行隔离。当前进程组、工作目录和资源限制不是不可信代码沙箱。
Polly、Halide、TACO、SPIRAL、Ginkgo 等领域工具仅在存在匹配需求时评估，
不作为通用平台依赖。

## 尚未接入的专项能力

- 分配频率与临时分配：可评估 Heaptrack，补充 Massif 的堆快照。
- 更深入的线程与任务追踪：已有应用区间和 perf switch-event 时间线，见
  [scheduler-timeline.md](../guides/scheduler-timeline.md)；唤醒、运行队列延迟和 DAG 因果
  追踪仍缺专用证据。Perfetto/Tracy 尚未接入，不将已有切换记录解释为等待原因。
- 编译器与指令分析：优化报告、llvm-mca 等需要明确构建产物及测量范围。
- 带宽与 NUMA：仅使用已探测且来源可信的事件，不从缓存 miss 推算带宽。

这些是扩展方向，不是已下载或已实现的能力。当前不引入 GUI 工具、替代分配器
或专用领域库作为默认依赖，也不自动改变目标构建和浮点语义。

## Agent Skill

项目使用一份原创主 Skill，指向统一 CLI 和按需加载的引用。
外部 Skill 不作为运行依赖；采用前需核对固定来源、许可证和实际执行脚本。
不能用通用优化提示词代替能力探测、正确性检查和实验比较。

## 借鉴的组织方式

Caliper 的采集/处理分离、Thicket 的实验维度保留、Perfetto 的带类型追踪组织
方式用于架构设计参考；平台没有将这些项目描述为已接入后端。
