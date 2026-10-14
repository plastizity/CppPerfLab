# Google Benchmark 适配验收

2026-10-14，Google Benchmark v1.9.4，GCC 8.5，WSL2。
范围是集成式微基准适配，不是任意可执行程序的 profiling。

## 验收门与实际结果

| 验收门 | 结果 |
|---|---|
| 编译两个链接真实库的 C++ benchmark | 通过，profile 构建；修正示例 const DoNotOptimize 警告 |
| 独立正确性验证 | CTest 20/20；两种 pair-count 算法用 Python Counter 验证 7 个规模 × 4 种分布 × 3 个种子 |
| 真实重复测量 | 两组均 completed；各 9 个重复样本，单次迭代 wall/CPU time 分开归一化为秒 |
| 计时范围 | 输入生成在循环外；输入复制与 pair-count 在循环内；不包含启动、编译或 Python 编排 |
| 原始证据 | 每个 run 保存 benchmark.json、stdout/stderr、执行参数、ELF 标识、环境、正确性及文件哈希 |
| 比较 | wall 与 CPU 两项比较成功；候选噪声超过政策阈值，返回 inconclusive |
| 空过滤器 | 实际拒绝，exit 3；无计时样本 |
| 正确性失败 | /bin/false 实际拒绝，exit 5；无计时样本 |
| 不可比实验 | 与 Hyperfine 全进程计时比较，exit 7；未混合不同语义 |
| 自动化回归 | 24 项 Python 测试通过，包含单位转换、排除聚合行、拒绝错误/多 case/空数据，以及安装 CLI 退出码 |

独立 oracle 测试共享同一算法源文件的 companion 可执行程序；这不构成
对所有可能 benchmark harness 缺陷的证明。样例使用整数语义，不代表浮点
收敛、误差或线程安全已验证。

## 实验结果

- baseline：`20261014T041022-a076abdc4cda`
- candidate：`20261014T041434-5d00497c7c2a`
- 原始数据：`.perflab/runs/<run-id>/` 下的 `benchmark.json` 和对应结果记录。
- 导出：`results/google-pairs-{baseline,candidate}.json`；
  `results/google-compare-{wall,cpu}.json`。

N=1024、random、seed=12345；朴素计数与排序计数比较：

| 指标 | baseline | candidate |
|---|---:|---:|
| wall 中位数/迭代 | 464.34 µs | 42.91 µs |
| wall CV | 2.85% | 15.45% |

观测中位数比约 10.82；候选 CV 超过 10%，所以性能结论为 **inconclusive**。
工具链功能验收通过，不等于该优化收益已验收。不能外推为端到端加速，
也不能据一个规模证明复杂度。后续若验收算法收益，应延长测量、交错 A/B、
覆盖多个规模和分布，并记录主机干扰；不能挑选最快结果。

## 重现命令

依赖构建见 [downloads.md](downloads.md)，然后：

```bash
cmake --preset profile -DPERFLAB_GOOGLE_BENCHMARK_ROOT="$PWD/vendor/google-benchmark-install"
cmake --build --preset profile -j 2
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_examples.py
bash scripts/install.sh
source scripts/env.sh
perflab plan --config workloads/google-pairs-baseline.json --json
perflab run --config workloads/google-pairs-baseline.json --json
perflab run --config workloads/google-pairs-candidate.json --json
perflab compare BASELINE_ID CANDIDATE_ID --metric time.iteration.wall --json
perflab compare BASELINE_ID CANDIDATE_ID --metric time.iteration.cpu --json
perflab report BASELINE_ID --format json
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
```

使用命令返回的 ID。原生 Google Benchmark JSON 的 aggregate 行不能算作
重复样本；每个 repetition 是迭代均值，不是单个迭代观测。
没有目标程序时的全局 probe 返回 integrated_target_required 属于正确行为。

验收中发现并修复 zipapp 默认入口丢弃 main 返回码的问题：安装脚本现在显式
SystemExit(main())。JSON 错误和 shell exit code 已通过真实失败调用共同验证。
