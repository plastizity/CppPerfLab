# CppPerfLab：开源工具调研与首轮取舍

调研日期：2026-10-14。本文用于确定独立平台的首批适配边界。来源优先采用项目上游文档、仓库或 Linux 手册。许可证只有在来源明确可确认时才写明，否则标记“待核实”。维护状态只描述可见的官方版本/文档情况，不以热度或提交数推断质量。

## 结论

首轮适配 Hyperfine 1.19.0（低干扰重复进程计时）、`perf stat`（wall duration、PMU 计数及交叉核对）、`perf record -e cpu-clock`（采样热点）和 Valgrind Massif（堆占用与分配调用路径），以 Python 3.11 标准库编排并保存 JSON 与原始文件。Hyperfine 官方 Linux 包已在本地解压，`--version` 验证通过；本地包 SHA-256 为 `d5550bbdc240d2aaa8f43ea2dc8b03a306afb30ea8464cd8b2faa9486ed1019e`，该指纹仅用于本地文件完整性识别，不是上游签名或认证。上述能力覆盖低干扰端到端计时、CPU 计数/采样和堆快照/调用树，且无需给目标程序链接 CppPerfLab runtime。`perf` 对硬件事件的支持取决于内核、CPU、虚拟化和权限；适配器必须逐事件探测，区分软件事件可用和硬件 PMU 事件不可用。Massif 是插桩工具，数据用于定位堆占用，不用于报告本机正常运行时长。

Google Benchmark 是需在目标工程中集成的微基准库，不能伪装成通用外部命令适配器；现已接入 v1.9.4 并完成独立 C++ benchmark 验收，见 [验收记录](google-benchmark-acceptance.md)。Hyperfine 与 `perf stat` 都能观察进程 wall time，但 Hyperfine 提供低干扰的多轮样本和稳健统计，`perf stat` 同时提供 PMU 计数及另一套计时证据，首轮两者互补并保留工具与计时语义标识。

| 候选 | 能力、格式与代价 | 平台及许可 | Agent 价值、首轮决定 |
|---|---|---|---|
| Linux `perf` [stat](https://man7.org/linux/man-pages/man1/perf-stat.1.html) / [record](https://www.man7.org/linux/man-pages/man1/perf-record.1.html) | `stat` 输出运行事件计数/比率；`record` 保存 `perf.data`，`report`/`script` 可读。PMU 事件可能受权限、虚拟机和硬件支持影响；采样有频率与栈回溯开销，计数器 multiplex 时需保留比例/运行时间信息。 | Linux 内核工具；Linux 主机最完整，WSL2/虚拟机的计数器能力不可预设，Windows 原生不适用。内核源代码 GPL-2.0-only（工具的发行/打包可能带依赖，分发许可需逐项确认）。 | 结构化 `stat` 适合统一指标；`perf.data` 作为原始证据保留。首轮纳入，按软件事件和硬件 PMU 能力分别探测。 |
| [Hotspot](https://github.com/KDAB/hotspot) | Linux `perf.data` 的 GUI，可启动采集、按进程/线程/时间浏览、反汇编；重点是人工探索而非无头数据接口。GUI/符号解析成本高于 CLI。 | 上游称 Linux perf GUI；GPL-2.0-or-later。Windows 原生不适用，WSL GUI 依赖图形环境。 | 对 Agent 非必要依赖；推荐作为人工检查 `perf.data` 的可选阅览器，不纳入核心执行链。上游持续有提交记录，但版本可用性以发行页核实。 |
| [FlameGraph](https://github.com/brendangregg/FlameGraph) | 将采样栈折叠为 folded stacks，再生成 SVG；要求采集器已有栈数据。工具本身不采集、不判断热点因果。输出 SVG/folded text，便于归档和文本过滤。 | Perl 脚本，可用于常见类 Unix 环境；核心 `flamegraph.pl` 明确采用 CDDL-1.0，并要求分发时包含 `docs/cddl1.txt`；仓库其他脚本需按文件查看头部许可证。Windows 使用取决于 Perl 环境。 | 是可选的 `perf script` 后处理/展示器，适合 Agent 附上可浏览证据；首轮后置。 |
| [Tracy](https://github.com/wolfpld/tracy) | 原生应用 profiler，支持 CPU/GPU/内存等时间线；需目标程序接入宏/客户端并构建，采集期间有插桩和连接开销。可导出 profile，主要交互界面用于时间线分析。 | 上游提供 Linux、macOS、Windows 支持和 Windows binaries；3-Clause BSD。GitHub 仓库有持续版本/代码活动可见，目标接入仍需自行维护宏和构建配置。 | 擅长线程/任务时间关系，适合后续可选 SDK Adapter；目标改造和多平台客户端生命周期使它不适合首批零侵入链路。 |
| [Hyperfine](https://github.com/sharkdp/hyperfine) | 独立命令重复执行、预热、统计离散度，支持 JSON/CSV/Markdown 导出；测的是进程级 wall time，启动成本会影响短程序。 | Rust CLI，MIT 或 Apache-2.0 双许可；Linux/macOS/Windows。WSL 可作为 Linux CLI 运行。 | 本地已验证官方 Linux 包 Hyperfine 1.19.0 的 `--version`；首轮适配外部 exe 的重复进程 wall time。包 SHA-256 仅作为本地文件完整性指纹，不代表上游认证。 |
| [Google Benchmark](https://github.com/google/benchmark) | C++ 微基准库，含参数化、复杂度估计、多线程、CPU/manual timers、自定义统计量及 JSON 输出；要求在项目内注册 benchmark，必须处理 setup、优化消除和计时边界。 | C++ 跨平台（具体编译器/平台取决于版本）；Apache-2.0。 | 面向 kernel/算法规模实验，是后续 CMake adapter 的主选。不能把 `benchmark` 命令当作任意外部程序 profiler；已完成 v1.9.4 单 case 重复测量适配与真实 demo；复杂度聚合、多线程等其他模式尚未适配。 |
| [Heaptrack](https://github.com/KDE/heaptrack) | Linux allocation tracing，记录分配事件及栈；可分析累计/临时分配及调用路径，产出压缩 profile，由 CLI/GUI 后处理。追踪会改变分配路径、增加开销；超大 profile 加载/查询成本高。 | Linux；KDE 项目，仓库使用 REUSE 多许可目录，须按文件核对；不假定 Windows/WSL 原生可用。 | 对分配频率和调用点比 RSS 更有解释力。可作为 Massif 的备选/后续适配；首轮选 Massif 是为了避免同时引入另一套解析与安装依赖。 |
| [Valgrind / Massif](https://valgrind.org/docs/manual/ms-manual.html) | Massif 采集 heap 使用曲线和分配栈树，`massif.out.*` 可由 `ms_print` 解析；默认不等于 RSS，也不包含所有直接页级映射。文档明确程序会显著变慢；不用于正常性能计时。 | Valgrind 支持的 Unix/Linux 平台和架构见官方支持表；WSL 取决于 guest 内核/架构，Windows 原生不适用。上游说明为 GPL-3.0-or-later；当前环境 `rpm -q valgrind` 报告 GPLv2+，应区分发行版包元数据与上游许可标签；独立头文件有不同许可。 | 可由 Agent 外部启动，原始文件适合存档，首轮专项适配。指标命名为 Massif heap bytes/峰值，不能标成 process peak RSS。 |
| [AddressSanitizer](https://clang.llvm.org/docs/AddressSanitizer.html) | 编译期插桩加运行库，查越界/UAF 等错误；可启用 allocator/deallocation stack tracing，但 sanitizer 构建的时间和内存数据不代表发布构建。 | Clang compiler-rt 支持平台见文档；常见 Linux/macOS/Windows 组合依工具链而异。LLVM Apache-2.0 WITH LLVM-exception。 | 正确性门，不是首轮性能 profiler；可作为候选优化回归的独立验证运行。 |
| [MemorySanitizer](https://clang.llvm.org/docs/MemorySanitizer.html) | 检测未初始化数据读取；上游给出典型约 3x slowdown、约 2x 实际内存开销；要求程序及依赖大范围插桩，输出诊断到 stderr。 | 上游列 Linux、NetBSD、FreeBSD；Windows/WSL 不承诺。LLVM Apache-2.0 WITH LLVM-exception。 | 高价值但依赖闭世界重编译，不是通用外部二进制能力；作为特定源码工程的 correctness profile 延后。 |
| [ThreadSanitizer](https://clang.llvm.org/docs/ThreadSanitizer.html) | 编译期插桩查数据竞争；上游典型 slowdown 5–15x、内存约 5–10x；诊断可符号化。其耗时不作基线。 | 上游支持 Android、Darwin、FreeBSD、Linux、NetBSD 的部分 64 位架构；Windows 未列为支持平台。LLVM Apache-2.0 WITH LLVM-exception。 | 并发正确性工具，不是锁性能/扩展性测量器。后续单列 sanitizer 构建配置。 |
| [mimalloc](https://github.com/microsoft/mimalloc) | 可替换 allocator，也可作为显式 API 链接；需控制 allocator 配置、线程数、工作负载和进程环境，观察时延、吞吐与内存指标。 | 上游覆盖 Windows/Linux/macOS 等，精确支持依版本/架构；MIT。官方仓库与 release 活跃可见。 | 是“比较分配器”的候选，不是 profiler；更换 allocator 可能改变行为和 RSS。待有 allocation-bound 证据再接 allocator matrix。 |
| [TCMalloc](https://github.com/google/tcmalloc) | malloc/new 替代实现，提供 tuning 和 heap introspection/profiling 能力；需链接或替换 allocator，比较时记录 allocator 和配置。 | Linux 等平台细节看官方 platforms guide；Apache-2.0。官方仓库活跃；官方构建以 Bazel 为主，CMake 为实验支持。 | 与 mimalloc/jemalloc 属同一评估维度；首轮不引入 allocator 选择策略，避免把特定 benchmark 结论泛化。 |
| [jemalloc](https://github.com/jemalloc/jemalloc) | 通用 allocator，提供统计、调优和 heap profiling；需链接/预加载且调试符号、构建选项影响分析。 | 主要用于 Unix/Linux 系统；项目许可按仓库 LICENSE 核实（BSD 2-Clause）；其上游维护/发布节奏需在启用时重新审查。 | 保留为兼容候选；首轮不默认纳入，需先确认本机发行版包与当前维护状态、目标系统 ABI。 |
| [llvm-mca](https://github.com/llvm/llvm-project/blob/main/llvm/docs/CommandGuide/llvm-mca.md) | 对指定 CPU 的汇编指令流用 LLVM scheduling model 估算 IPC、吞吐和执行端口压力；JSON 部分支持。它是静态模型，不模拟分支预测、cache miss 或前端取指，准确性受模型质量约束。 | 随 LLVM 工具链；Linux/macOS/Windows 可获得相应 LLVM 构建，处理器模型覆盖有限。Apache-2.0 WITH LLVM-exception。 | 当 perf 指向短热点且汇编是问题时，作为诊断佐证；不能代替实机测量。首轮推迟。 |
| [GCC optimization reports](https://gcc.gnu.org/onlinedocs/gcc/Developer-Options.html) | `-fopt-info`/missed/vectorization 等编译诊断输出到 stderr 或文本文件；报告的是编译决策，不是运行时成本。跨 TU 写同一个文件有覆盖风险。 | GCC 多平台；编译器 GPL-3.0-or-later with GCC Runtime Library Exception 等组件许可需区分，调用工具无需将报告代码并入平台。 | 适合 Agent 检查向量化/内联未发生原因；首轮作为手动外部证据，不用作 Adapter blocker。 |
| [Clang optimization remarks](https://clang.llvm.org/docs/UsersManual.html#options-to-emit-optimization-remarks) | `-Rpass`, `-Rpass-missed`, `-Rpass-analysis` 输出优化诊断；可保存优化记录供后处理。具体 pass 支持不全，报告不是动态性能证据。 | Clang/LLVM 多平台；Apache-2.0 WITH LLVM-exception。 | 与 GCC reports 对应，基于所用编译器选择；后续可归一为编译证据源。 |
| [LLVM BOLT](https://github.com/llvm/llvm-project/tree/main/bolt) | 基于采样 profile 的 post-link 优化器，重排二进制布局；要求支持的 ELF/链接元数据及采样输入，优化后需完整正确性与真实基准复测。 | 主要针对 Linux ELF；LLVM 开源许可 Apache-2.0 WITH LLVM-exception。 | 是优化动作而非分析器。首轮排除，避免将二进制重写纳入核心平台；未来可作为显式构建/变换实验。 |
| [Intel PCM](https://github.com/intel/pcm) | 命令行/API 收集 Intel CPU 的 uncore、内存带宽、功耗等硬件指标；依赖 CPU 型号、驱动/权限和采样约束。 | 上游列 Linux、Windows 等，Intel CPU 专属；许可证以仓库 LICENSE 为准（本次摘要未读许可证文本，待核实）。 | 可补 perf 对某些平台/uncore 指标的缺口；首轮只有探测到适配 CPU 与权限后才值得接入。 |
| PGO / LTO（[GCC optimize options](https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html)，[Clang Users Manual](https://clang.llvm.org/docs/UsersManual.html)） | PGO 利用训练运行 profile 决策，LTO 跨翻译单元优化；需要训练集/运行参数/编译器版本可重复，构建时间和产物变化显著。Profile 不匹配会降低代表性。 | GCC/Clang 的平台范围依目标 toolchain；工具许可证跟随编译器。 | 是构建变量，不是 profiler；作为实验构建维度保存 compiler flags 和 profile provenance，不自动开启。 |
| [LLNL Caliper](https://software.llnl.gov/Caliper/) | C/C++ instrumentation/measurement library；服务模块组合采集源、触发、缓存/聚合、输出，缺省不收集；可由 env/config/API 配置，输出 Caliper raw/JSON/文本。插桩/采样服务有各自开销。 | 上游面向 HPC/Linux 环境；跨平台边界按具体服务和依赖核验。BSD 风格 3-Clause（见 [LICENSE](https://github.com/LLNL/Caliper/blob/master/LICENSE)）。官方文档与版本化服务文档持续发布。 | **架构启发**：把“采集源、触发器、处理器、sink”作为可组合能力，而不是一个万能 profiler 枚举；声明服务依赖，并保留原始记录。SDK 接入要可选，后续再做。 |
| [LLNL Thicket](https://thicket.readthedocs.io/en/stable/) | Python 探索多维并行性能数据；把多次运行、规模、平台、工具等数据与 call tree 结合，便于聚合、筛选和比较；需要 Python 数据分析依赖。 | Python；MIT。仓库有开发工作流与公开文档，版本/依赖在接入时需固定。面向 HPC 数据集。 | **架构启发**：Experiment 是一等对象，需保留 run metadata、callpath、measurement source，并允许按平台/规模/工具维度查询；本平台的 JSON 协议应给后续 Thicket 类分析留出原始维度。首版不引入其依赖。 |
| [Perfetto](https://perfetto.dev/docs/) | 跨进程/线程时间线 trace，TracePacket/TrackDescriptor/TrackEvent 关联 track 和 slice；Trace Processor 支持 SQL、CSV/JSON 类导出。高分辨率 trace 的数据量和插桩成本需记录。 | C++ SDK 覆盖 Linux/macOS/Windows；Linux 可读取系统 tracing，WSL2 能力取决于内核 tracing 源。代码示例 Apache-2.0；完整仓库许可需按文件确认。 | **架构启发**：区分 timestamped event、track/线程归属和 duration slice；原始 trace 是关联证据而非单个汇总数。后续用于线程调度/跨进程视图，首轮不接入。 |
| [Coz](https://github.com/plasma-umass/coz) | Causal profiler，通过虚拟加速源码进度点估计优化某代码区域对整体吞吐/延迟的影响；程序需加 progress points/链接 profiler，需多次运行累积数据。报告是反事实估计，不等于实际代码优化收益。 | Linux `perf_event_open` 和 macOS private kperf；部分平台需要权限；BSD-2-Clause。上游明确其采样基础和系统条件。 | Agent 可用来为优化候选排序，但实验和进度点侵入明显；首轮推迟，且输出只能标为“优化潜力估计”。 |

## 架构采纳点

Caliper 的服务组织提示平台能力描述不应只写 `domain=cpu`：应写清数据源、触发方式、聚合/追踪处理与输出 sink 之间的依赖，允许一个分析计划组合多个服务。首轮 Adapter 可保持简单，但 Capability 记录应表达“可测什么、依赖什么、如何产出证据”。

Thicket 的多实验分析提示，单次 run 的数字不足以成为平台中心。统一 Experiment 需要携带输入、构建、机器、线程规模、工具版本和原始样本；聚合时保留 run 级记录及 callpath 身份，不能只存均值。借鉴其数据分析思想，不代表首版必须依赖其 Python 包。

Perfetto 的结构化 trace 提示，时间线数据应有明确的时钟、时间单位、track/线程归属、slice 边界及事件来源。未来可将 Perfetto trace 作为一种原始证据格式，并从中派生指标；不得把 trace summary 和 `perf stat` 的计数当作同一种测量语义。

## 首轮的可比性规则

- 比较 wall duration 时，记录目标程序/参数、输入标识、build ID 或二进制哈希、CPU/OS、线程环境、重复样本及工具版本；工具插桩运行与原生运行分开归档。
- `perf stat` 每个事件保留事件名、原始值、单位、运行/启用比例和失败原因；未获得硬件事件时只能报告不可用，不能用软件事件冒称 PMU 计数。
- `perf record` 样本用于热点归因，需归档 `perf.data`、采样事件/频率、call graph 方法、符号/Build ID 信息；采样占比不是精确耗时。
- Massif 报告为 heap profiling，另存 massif 原始快照与解析文本；不以 Massif 下的运行时间比较候选版本。
- 样本数少或运行波动大时只报告样本和差异，不声称确定提升。Hyperfine、perf stat、Google Benchmark 的计时对象和 setup 语义不同，协议必须保留测量工具和计时边界，禁止无条件合并。

## 官方参考索引

上表每行链接到上游仓库或文档。本次特别核验的设计材料： [Caliper service configuration](https://software.llnl.gov/Caliper/services.html)、[Caliper 配置教程](https://github.com/LLNL/caliper-examples/blob/master/tutorial/configuration.md)、[Thicket 文档](https://thicket.readthedocs.io/en/stable/)、[Perfetto TrackEvent 协议](https://perfetto.dev/docs/reference/trace-packet-proto)、[Perfetto Trace Processor CLI](https://perfetto.dev/docs/reference/trace-processor-cli)、[llvm-mca 限制与 JSON 说明](https://github.com/llvm/llvm-project/blob/main/llvm/docs/CommandGuide/llvm-mca.md)。

版本号、许可证及能力随上游和发行版可能变化；工具安装/分发前，应从固定 tag、具体 LICENSE 文件和本机 binary 的 `--version` 再核实。本文没有安装或验证这些工具。
