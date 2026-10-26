# 普通程序阶段观测、轻量计时与执行预算

这些能力复用已有 run/Adapter/Store/Comparison，不会重建测量系统。
独立代码通用 case 框架及配对区块调度尚在实施清单中，不能将本示例当作完整框架。

## 不启动程序的预算预览

```bash
perflab budget --config workloads/phase-text.json --json
```

此命令只加载/校验配置，不执行 probe、oracle、目标或构建。列出成功路径上每个
候选工具的执行组数、已知目标调用数和未知工作。auto 模式列候选，不声称选择或可用。
正确性脚本/逐次验证内部可调用多少次目标是未知的，不把一条命令等同于一次计算。
Google Benchmark 的注册列表会启动目标 main，副作用归目标所有；预热/迭代自适应。
Hyperfine 内部的进程预热/测量另列，不把它的一次工具启动当作一次目标执行。
`configured_timeout_budget_seconds` 是主要执行组 timeout 配置之和，不是预计耗时、
墙钟上界或准许额外运行的次数。探测、ELF metadata、可选/必需的 report 后处理、清理、存储不在这个和中；
额外进程在 stages 中单列，条件性数量保持未知而不是默认零。

现有 `plan` 保留会执行真实探测的语义，并附加同一预算对象。昂贵目标先运行
`budget`。预算不自动删掉预检或减少正确性覆盖。

## 普通程序接入

在 `measurement` 增加 `phase_report: "case.phases.json"`；目标参数中使用
`{run_dir}/case.phases.json` 输出每次运行自己的文件。格式可查询：

```bash
perflab schema phases --json
perflab metrics --metric phase.self_time --json
```

暂只支持每个样本对应一次目标执行的非 batch Adapter。只读批次最后一次输出
不能证明覆盖每次测量，所以拒绝 batch 接入。现有 Google Benchmark 单阶段
计数接口继续有效，不会静默转换范围或来源。

报告根部声明 version、scope、同一单调时钟时间域下的 start_ns/end_ns 和 stages。
每个阶段包含：id/name、parent_id、track_id、call_id、input_id、起止纳秒、
children_complete、result、correctness 和 work。名字不是预置业务枚举；平台不
识别模型类型。input_id 可空（未知），不是完整输入的替代品。分类如 uncertified
与 correctness=failed 是独立概念；正确性只允许 passed/failed/not_checked。
每个 work 项声明 value、unit 和 Measured/Estimated/Modeled，不把模型计数冒充实测。

直接父子须在同一轨道且区间包含，跨线程依赖不是此同步树格式的 parent 关系。
根覆盖时间取所有顶层区间并集；自身时间在 children_complete=true 时才计算为
父区间减直接子区间并集。不完整覆盖时自身时间 Missing，残余标为未归属时间。
多个线程/调用的累计包含时间、父子工作量不能重复相加冒充端到端墙钟总量。
工作计数同名跨调用须单位/证据状态一致且无遗漏；缺失不能自动补零。

所有值保留来源文件，run.evidence 中有原始文件及哈希。明确的 phase correctness
失败会拒绝实验性能验收；局部比较还要求相应阶段均显式 passed。目标自身汇报
不是独立数值证明，仍须原有 oracle，报告不扩大其覆盖声明。

```bash
perflab analyze RUN_ID --section phases --input-id sample-1 --result success --limit 20 --json
perflab compare BASELINE CANDIDATE --metric phase.inclusive_time \
  --scope 'text-function; phase=["call","invoke"]' --json
```

精确 scope 选择不放松原有输入、环境、工具及单位兼容检查。不提供 scope 而存在
多个同名阶段指标时拒绝比较，不能随便挑一行。JSON 与 HTML 来自同一分析结果。

## 可选 C++11 头文件

`include/cppperflab/phase.hpp` 无 Google Benchmark 依赖。显式添加 include 目录，
不安装全局 SDK、不修改使用方构建。先注册字符串和计数器；Scope 热路径只读时钟、
写预分配记录和更新索引。容量耗尽抛错，不静默丢弃昂贵样本。

```cpp
perflab::PhaseRecorder recorder(capacity);
auto phase = recorder.define_phase("search");
auto input = recorder.define_input("sample-1");
auto success = recorder.define_result("success");
std::size_t id;
{
    perflab::PhaseRecorder::Scope timer(recorder, phase, input);
    id = timer.id();
    output = Invoke(input_data);
}
recorder.mark(id, success, Validate(output));
recorder.write(report_path, "declared-computation-window");
```

recording/校验/汇报都有成本；目标报告起止范围可能包含其间的处理。函数调用区间
不包含退出 Scope 后的标记/校验，但这些会影响更外层总时间，不能声称免费。
单个 recorder 不支持并发写入；不能为省事添加全局锁而改变目标竞争行为。
递归须正确嵌套，所有 Scope 完成后才能导出。生产函数内的正常编译优化不被 SDK
禁止；微基准另外需要可观察输出和合适的优化屏障/成熟 benchmark 适配。

## 独立合成验证与开销校准

```bash
cmake --build build/profile --target phase_text phase_calibration phase_sdk_fixture
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_phase_example.py
bash scripts/install.sh
source scripts/env.sh
perflab budget --config workloads/phase-text.json --json
perflab run --config workloads/phase-text.json --json
build/profile/examples/cpp/phase_calibration 20000 > /tmp/phase-calibration.json
```

文本由脚本合成。读取文件在 recorder 创建之前，invoke 与 oracle 使用不同实现，
每次调用在计时块外校验；字节工作量明确按 std::string 字节数而不是 Unicode
字符数计算。层级例子是 call/invoke，不代表真实业务流程。CppPerfLab 仍通过
已有 perf-stat duration_time Adapter 记录原生全进程时间及原始证据。

校准交替空循环和带 recorder 的循环，保留九组原始秒数和每调用增量纳秒估计；
捕获时长在计时循环外被消费，避免将记录完全当死数据。它不包含分配、注册、
mark、set_work、输出，也不是任意优化级别/硬件/负载通用的成本。出现负差值
同样保留，不钳制、不从真实负载耗时机械扣除。

## 分开的结论

Comparison 新增 decisions：正确性、可比性、样本/CV 筛查、回退门槛、预设性能
目标和加速证据。回退门槛复用现有 acceptance 去掉 min_speedup 后的同一算法；
加速证据在统计筛查通过后，将方向一致的描述性收益区间与 1 比较。
因此 PASS + no_clear_change 合法，不能解释成确认加速。未定义方向的诊断指标
不自动变成优化评分。旧 acceptance/classification 保留兼容；新报告分别展示，
不通过任意综合分或一个布尔值掩盖资源回退、缺失数据或数值错误。
