# 算法级优化工具与 Agent Skill 调研

调研日期：2026-10-14。范围是算法规模实验、自动调优项目和可复用 Agent 指引；本文是调研与架构建议，不表示候选已安装或接入。仓库/文档链接指向上游当前页面，除标注的 ShinkaEvolve release 外，未固定到 commit。许可证未从上游明确核实时标为“待核实”。

## 结论与实施边界

近期优先补齐“同一真实 workload 的规模序列”能力：新增 scale-series 描述与分析，调用既有 `lab.run` 执行每个点，复用现有 adapter、实验持久化、证据归档和比较逻辑。保留每个规模的原始样本、输入/构建/工具身份和正确性结果；不另建执行器或存储。有限规模点只支持经验模型比较，不证明渐近复杂度。自动代码生成/变异作为后续独立阶段，当前没有经审查的候选代码沙箱、资源配额及稳定网络/模型服务条件，不安装或运行此类系统。

| 项目 | 能力、格式及限制 | 平台/许可/安装与维护证据 | 决定及 Agent 价值 |
|---|---|---|---|
| [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve) | LLM + 演化搜索；维护程序种群、并行评估、档案/岛屿；候选通过用户 evaluator 测分。 | README 标 Apache-2.0，提供 pip/uv 与 `npx skills add`；可见 release v0.0.4 commit `127fda9`，当前分支未固定。依赖模型服务/API 或本地推理，评估会执行候选代码。 | P3 暂缓。Skill 的 setup/run/inspect 文本已读：run 有 worker 配置、结果目录与批次确认；仍需隔离任意候选程序及控制 API 成本。可作为未来受控搜索后端，不作首轮测量工具。 |
| [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) | 开源演化优化，结合候选生成与 evaluator；支持并行评估、超时/重试等配置。 | 官方 pyproject 标 Apache-2.0，Python >=3.10；声明依赖 OpenAI SDK、PyYAML、NumPy、tqdm、Flask、dacite。未固定 commit。配置中 CPU/内存限制尚未实现；evaluator/候选执行路径涉及 subprocess。 | P3 暂缓。不能把普通子进程当安全沙箱；许可证和 pip 可安装性不能替代资源隔离、代码执行审查和预算控制。 |
| [pyATF](https://github.com/atf-tuner/pyATF) | Python 参数空间搜索/自动调优，用户提供配置及代价函数；可选 GPU/OpenCL 支持。 | 上游提供 `pip install pyatf`；Python 3.9+。许可证应以固定版本 LICENSE 再核实（本次未据一手 LICENSE 确认）；可见上游组织有近期活动，但未固定 commit。可选后端有额外依赖和设备要求。 | P2 候选。适合已明确的参数搜索问题；当前先实现平台自己的规模实验与候选评分协议，避免引入第二套执行/搜索语义。 |
| [OpenTuner](https://github.com/jansel/opentuner) | 面向领域的参数表示、多目标代价和多种搜索技术组合；本地 SQLite 保存调优过程。 | 上游 README 给出 pip 安装和 Python/sqlite 前置条件；MIT。仓库可见内容较偏传统/旧式工作流，当前维护状态与兼容性未确认；未固定 commit。 | P2/P3 评估候选。多目标与搜索 ensemble 有参考价值，但其数据库/调优生命周期不应与 CppPerfLab 实验存储重复。先保留 scorer/planner 接口。 |
| [AutoPas](https://github.com/AutoPas/AutoPas) | 粒子模拟领域的 C++ 自动参数选择/性能调优库。 | BSD-2-Clause；上游有 v2.0.0 release。C++ 项目依赖/集成，不是通用 CLI 调优器。 | 不作为通用依赖。仅目标工程确属其粒子模拟使用场景时再评估；其在线选择思想可作领域适配案例。 |
| [Google Benchmark 用户指南](https://github.com/google/benchmark/blob/main/docs/user_guide.md) | 目标程序内 C++ 微基准；重复、预热、JSON/CSV、计数器和复杂度拟合（用户提供复杂度函数，可输出拟合系数/误差）。需要目标工程集成，不能当外部命令适配器。 | Apache-2.0；上游有 CMake/构建说明。已下载并适配 v1.9.4，真实双实现验收见 [记录](google-benchmark-acceptance.md)；仅接入单 case 的 wall/CPU 重复测量。 | scale-series 可借鉴其重复与误差表达；平台仍以外部真实 workload 为主，微基准结果标注边界，不等同端到端收益。 |
| [LLVM Polly](https://polly.llvm.org/) | LLVM 多面体优化/分析：适用于可识别的规则循环区域，可做 loop transformation、并行/SIMD 相关优化；受 SCoP/canonical loop 结构限制。 | LLVM Apache-2.0 WITH LLVM-exception；随 LLVM 工具链使用。需检查具体编译器构建是否包含。 | 后续针对热点 kernel 验证；不是任意 C++ 源码自动优化器。 |
| [Halide](https://github.com/halide/Halide) | 算法与执行 schedule 分离，包含 autoscheduler 与代码生成；主要面向图像/数据流水线。 | 主仓库 MIT（子目录许可需逐项看）；CMake/包方式集成。 | 参考其显式 schedule 搜索与正确性分离；仅在目标 workload 属数据流水线时作专用集成，不纳入核心依赖。 |
| [TACO](https://github.com/tensor-compiler/taco) | 稀疏/稠密张量代数编译与代码生成，重点是格式/计算表达。 | 上游仓库有 CMake 构建说明；许可证本次未从 LICENSE 独立确认，待核实；未固定 commit。 | 稀疏张量领域可作为专用后端或被测项目；不等同通用 C++ 调优器。 |
| [SPIRAL](https://github.com/spiral-software/spiral-software) | 数学变换领域的程序生成、平台适配和自动性能调优；产出 C/C++ 等代码。 | 官方文档描述 BSD 风格许可；仓库说明支持 Linux/macOS/Windows。安装/依赖链较专门，未固定 commit。 | 只在 FFT/线性变换等匹配的数学领域评估；不引入通用平台。 |
| [Ginkgo](https://github.com/ginkgo-project/ginkgo) | 高性能稀疏线性代数 C++ 库，提供 CPU/GPU 后端和 benchmarks。 | BSD-3-Clause；C++17+，按上游 CMake 说明构建。 | 不是自动调优框架；未来可作为独立真实 workload/领域适配对象，不作为 CppPerfLab 依赖。 |

## 外部 Agent Skill 核验

只读上游提供的 Skill 文本及其声明的脚本入口；未安装、未复制，也未运行外部脚本。上游主分支可变，因此这些审查不是固定版本的供应链审计。

| 来源 | 实际内容与安全/集成检查 | 采用意见 |
|---|---|---|
| [rollysys/cpp-perf-skills](https://github.com/rollysys/cpp-perf-skills) 的 [SKILL.md](https://github.com/rollysys/cpp-perf-skills/blob/main/skills/cpp-perf/SKILL.md) 和 [cpp-perf.md](https://github.com/rollysys/cpp-perf-skills/blob/main/skills/cpp-perf/cpp-perf.md) | SKILL.md 仅含 frontmatter/触发描述；主要工作流在约 588 行 cpp-perf.md，覆盖基线、profile、生成 benchmark/correctness harness、反汇编及重测。还建议自动修复构建、生成 shell 命令、跨编译后 SSH 运行。仓库当前未见明确 LICENSE，不能据此复制；未固定 commit。命令插值/远端执行和自动构建修复须在具体命令审阅、授权及隔离下才可执行。仓库有 C++ profiler，但本次未审计其源文件。 | 仅吸收基线→分析→正确性→复测流程；不采用其生成/远程执行脚本或未经验证的默认样例。 |
| [juburr/mad-skills/cpp-performance](https://github.com/juburr/mad-skills/tree/main/cpp-performance) 的 [SKILL.md](https://github.com/juburr/mad-skills/blob/main/cpp-performance/SKILL.md) | 已读 208 行 Skill：先算法/布局/分配/并行/SIMD，再按证据选工具；强调不以一次结果宣称加速、不用 fast-math 代替正确性。列出 `check-tools.sh`、perf、Google Benchmark、编译器 remarks 等脚本及参数。脚本正文链接本次未能取得，故其命令拼接/副作用尚未安全审计；目录含 LICENSE，但本次未据固定版本确认许可。 | 方法论可参考；不运行或安装脚本，待固定 commit 并审阅每个脚本后再决定。Skill 本身不能取代 CppPerfLab 的工具发现、执行、JSON 协议及历史比较。 |
| [ShinkaEvolve Skills](https://github.com/SakanaAI/ShinkaEvolve/tree/main/skills) | 实读 `shinka-setup`、`shinka-run`、`shinka-inspect`：setup 生成 evaluator/初始候选并 smoke test；run 检查模型、设 worker、写 results/SQLite，按批次继续；inspect 用 Python helper 排名并生成 Markdown。setup/run 可创建或执行用户/模型生成代码；pip 与模型/API 凭据有外部依赖，`npx skills add` 可改变全局 Skill 目录。上游 Apache-2.0，release v0.0.4 commit `127fda9`；Skills 在当前分支未固定。 | 不安装。待 P3 沙箱、预算及依赖固定后按固定 tag 做专项审计；其结果查看器不取代本平台统一 run/证据格式。 |

本项目主 Skill 应自行编写并指向稳定 CLI、能力 JSON、协议字段和失败分类。这样 Agent 的工作流与 CppPerfLab 的真实命令、结果语义保持一致，不复制外部 Skill 的脚本清单、独立数据库或某个工具的专属流程。若当前 Agent 通过仓库指令发现技能，则用根目录 `AGENTS.md` 显式路由到项目 Skill；`.agents` 当前只读，不假定存在自动 UI 注册或外部 Skill 安装机制。

## 规模序列与候选比较建议

1. **Scale-series 复用 `lab.run`**：输入定义显式 size 参数及每个 size 的 workload 参数/数据标识；每个点作为普通 run，携带相同源码/构建/机器/线程/工具配置。先验证每组输出正确，再对每个点重复测量；原始样本和异常状态不被汇总覆盖。对可能有状态或热缓存效应的 workload，记录执行顺序，并采用多轮或交错次序降低漂移影响。
2. **经验模型而非复杂度证明**：提供可选候选模型（如常数、log n、n、n log n、n²），比较残差/归一化误差、参数和样本波动；模型名称是有限数据上的描述性拟合，不宣称算法渐近阶。规模点太少、范围太窄、输入分布变化或噪声重时，报告“证据不足”，不排名复杂度结论。Google Benchmark 的复杂度报告可作为拟合误差输出的设计参考。
3. **候选实现 A/B**：相同输入规模和构建条件下分别调用 `lab.run`，保留每轮结果与 correctness gate；对比前先报告可比性差异和分布/波动，不能仅比较均值。平台给出观测事实、拟合/归因推断和未验证假设的不同字段。
4. **调优分期**：P1 完成规模序列、经验拟合和候选比较，无新依赖；P2 在出现明确参数搜索需求后评估 pyATF/OpenTuner 或领域库；P3 才考虑 ShinkaEvolve/OpenEvolve 等源代码生成/变异。P3 前置条件包括固定依赖/版本、显式成本与停止预算、正确性 oracle、隔离工作树及真正的 OS/container 资源限制、可终止的子进程治理和审查候选变更。若网络/API 不可用或隔离不足，应拒绝启动搜索，保留可插拔的 planner/scorer/evaluator 接口，不伪装已完成调优。

## 核验来源与边界

主要来源均为上游项目仓库、README、文档、Skill 或配置文件（各行已链接）。许可证及安装命令应在集成时对固定 tag 的 LICENSE、依赖锁文件和目标平台重新核实。本文没有安装第三方项目，也没有运行候选 Skill 脚本；网络不可用的脚本内容明确记为未审阅。上游分支、依赖版本与维护状态可能变化。
