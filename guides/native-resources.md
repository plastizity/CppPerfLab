# 原生进程资源测量

`native-resource` 使用项目自有的小型 Linux helper 对目标的直接子进程调用
`wait4`，采集一次调用的资源数据，不依赖硬件性能计数器。它复用现有 Run、
正确性检查、原始证据和 Comparison；项目仍须提供可信 oracle 和可比工作负载。

## 显式构建与能力发现

以下只用 GCC 编译仓库中的 helper，不下载依赖，也不编译使用方项目：

```bash
python3.11 scripts/build_native_resource.py
bash scripts/install.sh
source scripts/env.sh
perflab tools probe --tool native-resource --json
perflab schema native-resource --json
perflab metrics --metric memory.rss_peak --json
```

helper 安装到仓库内 `.local/bin/perflab-resource`；构建信息和源、二进制
指纹记录在 `build/native-resource.manifest.json`。`source scripts/env.sh` 将
该路径加入当前 shell 的 PATH，并在变量未预设且 helper 可执行时设置
`CPPPERFLAB_NATIVE_RESOURCE_PATH`。也可显式通过该环境变量
`CPPPERFLAB_NATIVE_RESOURCE_PATH` 指向经审查的 helper 绝对路径；两种方式
均不修改全局配置。probe 返回实际可用性；仅构建或注册不保证采集成功。

## 配置和查询

在现有完整 request 中将 `measurement.tool` 设为 `native-resource`，
`measurement.options` 设为空对象，其余 workload、target、正确性和重复次数
仍由使用方事先声明。例如下面只展示 `measurement` 片段：

```json
{
  "tool": "native-resource",
  "repetitions": 7,
  "warmups": 0,
  "timeout_seconds": 30,
  "options": {}
}
```

使用实际 request 路径和返回的 ID，先查执行预算，再运行和查询：

```bash
perflab budget --config REQUEST.json --json
perflab run --config REQUEST.json --json
perflab analyze RUN_ID --section observations --metric memory.rss_peak --json
perflab compare BASELINE_ID CANDIDATE_ID --metric memory.rss_peak --json
```

比较遵循原有正确性、证据、范围和环境条件；重复次数与门槛应在实验前按
工作负载和成本确定，示例数值不是平台默认。可选 `work_report` 的数据格式
和使用方责任与 [数据搬运指南](data-movement.md)相同。

## 范围与指标

| Metric ID | 单位 | Linux wait4 来源 |
|---|---|---|
| `memory.rss_peak` | bytes | `ru_maxrss`，Linux KiB × 1024 |
| `time.cpu.user` | seconds | `ru_utime` |
| `time.cpu.system` | seconds | `ru_stime` |
| `os.minor_page_faults` | count | `ru_minflt` |
| `os.major_page_faults` | count | `ru_majflt` |

五项是指定子进程 `wait4` 的 Measured 数据，不采用累积
`RUSAGE_CHILDREN` 的前后差值。范围包含 fork/exec 启动及已等待的后代资源；
RSS 是该接口的高水位，不是进程树同时 RSS 总量、堆大小或物理独占内存。
用户态和内核态 CPU 时间来自操作系统 `timeval`，微秒字段不保证一微秒精度。
原始零值保留；以零作分母的比值比较保持 INCONCLUSIVE，不加 epsilon。
在配对 Study 中，可在执行前将特定 `secondary_metrics` 的 `gate` 明确设为
`informational`，保留零值不可比及空评分，避免该信息项阻断其他必要比较；
主指标、正确性及 Claim 中必要条件不能绕过。Case Study 也支持逐项预声明。
详细语义见[配对 Study](paired-studies.md)和[文件驱动 Case](file-case-framework.md)。

本轮 helper 已经独立审阅、完成四项实际测试并只编译平台自身的小型 C 工具；
probe `20261027T061448-eef906fd27c6` 成功。旧资源 Study
`20261027T062025-3238cf678980` 的 12 个 Run 全部完成，但 system CPU 和 major
fault 零值使 required 比值比较失败，原始记录保留且不改写；原生计时 Study
`20261027T062012-85eca3696bf7` 的 12 个 Run 已完成。预先登记信息项的资源
Study `20261027T064807-c2f1fadc9b08` 完成 12 个 Run；其中 system CPU 和 major
fault 的零值保留为不可比、INCONCLUSIVE、空评分，没有阻断其他必要比较。
联合评估 Study `20261027T064817-d423f1497582` 完成 6 个 Run、8 个正式样本，
正确性和预声明验收为 PASS，仍不自动采纳优化。完整结果及限制见
`.perflab/validations/native-resource-20261027/verification.json`，结果只对应该
机器上的合成工作负载。不能事后改旧实验的门槛宣称通过。

该联合评估使用相同程序和请求的 A/A 对照，仅有两个配对区块，且演示策略
允许较大波动。原始时间比较实际给出了 `SUPPORTED_IMPROVEMENT`；平台保留
这个统计结果，但它不能证明代码优化收益。这也说明少量样本和宽松门槛不能
代替独立确认；正式验收须事先确定适用的校准、重复次数和稳定性要求。

当前工作区保留当次配置，可在确认 helper、目标及输入仍在原位置后先查看
预算，再按需要重新执行；每次 `study` 都创建新的实验：

```bash
perflab budget --kind study --config .perflab/validations/native-resource-20261027/configs/resources-informational.study.json --json
perflab study --config .perflab/validations/native-resource-20261027/configs/resources-informational.study.json --json
perflab budget --kind study --config .perflab/validations/native-resource-20261027/configs/joint.study.json --json
perflab study --config .perflab/validations/native-resource-20261027/configs/joint.study.json --json
```

这些归档配置包含工作区绝对路径，联合评估还依赖原本地样本集合；迁移到其他
环境前需复制并改成该环境已审查的路径和集合。新实验应先固定策略和预算，
不能依赖前次结果保证重复通过。

该 helper 不发布 wall time；端到端速度请用独立的 `perf-stat` duration 或
Hyperfine profile 测量，并保留不同采集边界。不能将独立 profile 的数据拼成
同样本硬件派生比值，也不能由 RSS 或缺页数推出物理 DRAM 流量、内存泄漏或
自动确定瓶颈原因。LLC 事件、Uncore、物理 DRAM 与专用采样仍受实际硬件和
虚拟化能力限制；见 [数据搬运诊断](data-movement.md)。
