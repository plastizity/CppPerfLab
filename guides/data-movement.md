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

## 分层缓存与数据 TLB 原始事件

可通过相同的 `perf-stat` 事件配置申请六项 Linux 通用缓存事件；这些是原始
计数，不新增层级 miss ratio，也不会把原有 `cpu.cache_misses` 改称 LLC miss。

| Metric ID | perf selector | 请求的事件 |
|---|---|---|
| `cpu.l1d_loads` | `L1-dcache-loads` | L1 数据缓存读访问 |
| `cpu.l1d_load_misses` | `L1-dcache-load-misses` | L1 数据缓存读未命中 |
| `cpu.llc_loads` | `LLC-loads` | 最后一级缓存读访问 |
| `cpu.llc_load_misses` | `LLC-load-misses` | 最后一级缓存读未命中 |
| `cpu.dtlb_loads` | `dTLB-loads` | 数据 TLB 读访问 |
| `cpu.dtlb_load_misses` | `dTLB-load-misses` | 数据 TLB 读未命中 |

例如，先针对目标机器探测 L1 读事件及其强组，再在 request 的
`measurement.options.events` 和 `event_groups` 中采用同一 selector 列表：

```bash
perflab schema event-descriptor --json
perflab metrics --metric cpu.l1d_load_misses --json
perflab tools probe --tool perf-stat --events L1-dcache-loads,L1-dcache-load-misses --event-group L1-dcache-loads,L1-dcache-load-misses --json
```

其他两层同样需分别实际探测。静态 Registry 的 `event_descriptor` 描述的是
Linux 请求语义，并不保证该 CPU、内核、虚拟机或权限支持事件，也不保证请求
名称在不同平台统计相同的硬件事件。实际 probe 和 Run 保存
`event-descriptors.json`，记录可见的 CPU 身份、微码、内核、perf 版本、PMU
type/format 和实测 selector 的过滤条件（如 `:u`）；不可读取的信息标为 Missing。
各新指标样本携带 descriptor ID、版本与语义 key。比较、Claim、摘要和算法拟合
需要一致的事件语义与上下文；旧的通用缓存指标及 IPC 不要求补造这些描述符。

Linux `PERF_TYPE_HW_CACHE` 的 ABI type 是 3，config 编码表示请求的缓存、读
操作与访问/未命中类型；它不是 sysfs 中某个实际 PMU 的 type（例如 4），不能
据此猜测厂商事件码。当前平台无法取得内核翻译到物理 PMU 的映射时，将该
映射标为 Missing；这不等于已经成功采得的原始计数是 Unsupported。详细定义
见 [Linux perf UAPI](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/perf_event.h)
和 [perf_event_open 手册](https://man7.org/linux/man-pages/man2/perf_event_open.2.html)。

本次 WSL2 guest 实际完成六个单事件和三个双事件强组探测：L1D 和 dTLB 的四个
单事件、两个强组可用；LLC 的两个单事件和强组原始 perf 报告 `ENODEV`，不支持。
早期 probe 的 `parse_failed` 分类作为历史原样保留；补充更准确的 Unsupported
原因后，正式 LLC Run `20261027T043550-4e9030bf80ce` 在预检前拒绝，正式样本
为零。原始记录见 `.perflab/validations/dme-p1-20261027/probes.json`。本结果
只适用于当次 guest 环境，不是原生 Linux 硬件资格认证。

## 分层事件的配对实验配置

先按下方合成示例生成配置，再复制已有的 `pointer-32768-counts` 模板，分别
替换两侧 request 的事件和 Study 的比较指标。以下只生成配置，不执行目标：

```bash
python3.11 - <<'PY'
import json
from pathlib import Path
out = Path('workloads/dme').resolve()
source = 'pointer-32768-counts'
for label, events, primary in [
    ('l1', ['L1-dcache-loads', 'L1-dcache-load-misses'], 'cpu.l1d_load_misses'),
    ('dtlb', ['dTLB-loads', 'dTLB-load-misses'], 'cpu.dtlb_load_misses'),
]:
    study = json.loads((out / (source + '.study.json')).read_text())
    for side in ('baseline', 'candidate'):
        request = json.loads((out / (source + '-' + side + '.request.json')).read_text())
        request['measurement']['options'] = {'events': events, 'event_groups': [events]}
        path = out / ('p1-' + label + '-' + side + '.request.json')
        path.write_text(json.dumps(request, indent=2) + '\n')
        study[side] = str(path)
    study['name'] = 'p1-' + label
    study['metric'] = primary
    study['secondary_metrics'] = [entry for entry in study['secondary_metrics'] if entry['metric'] == 'work.items']
    (out / ('p1-' + label + '.study.json')).write_text(json.dumps(study, indent=2) + '\n')
PY
perflab budget --kind study --config workloads/dme/p1-l1.study.json --json
perflab study --config workloads/dme/p1-l1.study.json --json
```

按需要将末尾的 `p1-l1` 替换为 `p1-dtlb`；先逐组探测及查看预算。每个
Study 独立采集，不能把不同 profile 的事件当作同一样本配对。若目标无法
收集某组，保留 Unsupported 及原始诊断；不自动替换其他事件。示例配置阈值
只用于演示，正式对照须预先决定适用门槛。

本轮真实合成实验分别完成 native `20261027T043522-ac3ca6a488a0`、L1D
`20261027T043530-c14cb4f4c9cb` 和 dTLB `20261027T043541-23c755f2e0c4`
三个独立配对 Study，每组 3 个 ABBA 区块、12 个 Run，共 36 个正式样本；
证据及完整配置见 `.perflab/validations/dme-p1-20261027/verification.json`
和同目录 `configs/`。可采得的原始计数为 Measured，物理 PMU 映射仍为
Missing。三个区块只用于接口验证，不据此宣称稳定收益或因果关系。

另一次显式 `runtime_identity` measurement 观察 Run
`20261027T043551-18722671e171` 记录 38 个目标快照、8 个映射对象；期望
对象门为 `NOT_REQUESTED`。它不证明所有库从启动时已加载，也不能补造先前
实验的加载记录；观察本身的扰动未量化。8 条映射记录中 7 条指纹为 Measured，
一条早期 `/usr/bin/perf` 指纹为 Invalid：路径身份与映射的设备/inode 不一致；
另一条 perf 记录及目标和已观察的 libc、libgcc、libm、libstdc++、ld 指纹为
Measured。保留原始 Invalid，不修复或补造；详情见同目录 `identity.stdout.json`。
详见
[实际加载身份](runtime-identity.md)。

## 内存控制器流量能力预检

当需要物理内存流量证据时，可独立查询平台当前能力并执行只读元数据发现：

```bash
perflab metrics --domain D05 --json
perflab schema memory-controller-readiness --json
perflab tools probe --capability memory-controller-traffic --json
```

`--capability` 与 `--tool`、`--events`、`--event-group` 互斥。此预检只读取
sysfs/proc 元数据并归档 `uncore-readiness.json`，不执行 perf 或目标程序。
可见的 PMU 列表及候选项的 type、format、events 编码、unit、scale、cpumask、
cpus、代表 CPU 拓扑、paranoid 和 CapEff 等原始信息分别保留；读取不到的字段
为 Missing。目录不可读时不能据此声称没有设备，发现候选也不证明覆盖所有控制器。

缺 unit 或 scale 为 Missing，无法识别的单位换算为 Unsupported，损坏元数据为
Invalid；不默认每个事件是 64 字节。尚未执行实际权限探测时权限为 Missing，
不能仅凭 `CapEff` 为零或 `perf_event_paranoid` 为 2 推断拒绝。相关字段语义
见 [Linux PMU sysfs ABI](https://raw.githubusercontent.com/torvalds/linux/master/Documentation/ABI/testing/sysfs-bus-event_source-devices-events)
及 [Linux perf 安全说明](https://www.kernel.org/doc/html/latest/admin-guide/perf-security.html)。

当前平台没有内存控制器流量采集器或物理 DRAM 数值指标：Registry 的
`capability_gaps` 标记当前采集器 Unsupported；现有来源 Run 未归档物理流量时，
报告中的该次测量证据是 Missing。此能力状态不能回填成旧实验的硬件状态。
即使找到候选 PMU，当前采集器仍为 Unsupported；控制器/插槽/系统共享流量
不能直接归因到目标进程。平台不会因此自动执行系统范围 perf、安装工具或提权。

本轮 WSL2 guest 只读预检 `20261027T051550-76932ad59c67` 记录了 8 个可见
PMU、零个 IMC/UMC 候选：清单为 Measured，该环境的测量能力为 Unsupported
（`no_visible_candidate`），采集器为 Unsupported（`collector_not_implemented`），
权限未探测、为 Missing。证据见
`.perflab/validations/dme-p2-readiness-20261027/discovery.stdout.json`；未执行
目标、计数器测量或编译。存在丰富候选的原生主机路径尚未真实验证，目前仅有
fixture 测试；此结果不证明宿主没有内存控制器。

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

`work_report` 支持非批量 `perf-stat` 和 `native-resource`；其 `path` 是测量目录内的
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
即使通过 `native-resource` 采集工作量，硬件事件的单位工作量派生仍需满足
同调用、有效硬件事件组及覆盖率规则，不能跨 profile 拼接。

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
