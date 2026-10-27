# 实验主张、执行状态与分层确认

本指南介绍可选的 Claim、Execution State 和 Study Workflow；它们复用普通
Run/Study、已有工具与 Comparison。使用方负责定义有效任务、状态、独立验证
和实际收益门，平台记录并检查明确的契约与证据。结果的适用范围始终受已选
输入、环境及验证覆盖限制；可追溯性不证明统计独立或普遍科学正确。

## 声明与证据

在普通 request 中可选声明 `claim` 和 `execution_state`；分别有自己的
`schema_version: 1`。通过 `perflab schema claim --json` 和
`perflab schema execution-state --json` 读取完整字段。省略两者的旧请求和
结果继续使用原有路径；没有契约时是 `NOT_CONFIGURED`，不补造主张或状态 PASS。

Claim 选择 `task_cost`、`unit_efficiency` 或 `mechanism_hypothesis`，明确
主指标、可选精确范围、假设、适用范围、允许变化和所需条件。条件来源可为
`correctness`、`execution_state`、`work_metric` 或 `declaration`。工作条件
必须明确对应指标及其范围；`unit_efficiency` 必须包含必要的 work_metric
条件，并须有同样本、同范围、可比的实际计数。当前可用于该工作量门的单位
为 `count`、`bytes`、`byte`、`FLOP`；未知单位及时间/比例/速率不会冒充计数。
用户确认的是其业务含义，平台不推断未记录的工作。

单纯 `declaration` 是使用方声明，不是已验证证据。它若为必要条件，缺少
独立证据时不会自动 PASS；可选条件不足限制对应解释。任务总成本的主张可以
允许合法工作量减少，不能据此自动推断单位工作效率或根因。`allowed_changes`
记录处理变量，不自动绕过已有可比性检查；例如命令参数变化仍需通过现有
`allow_command_change` 明确处理，且其余条件和语义仍需匹配。

报告将原统计的改善与契约状态、主张验收分别呈现：检查 `validity`、
`claim_acceptance`、`decisions.improvement` 和 `decisions.claim_support`。
只有比较指标及精确范围与主张的主指标一致，才适用该主张。比较其他指标时，
`validity.claim_applicable=false`；共同必要条件可以通过，原统计及满足既有
条件的归一化成本仍保留，但 `claim_acceptance=INCONCLUSIVE`，不能据此声称
主指标主张成立。机制仍是 `HYPOTHESIS_ONLY`，必要条件未通过时不能获得有效
归一化收益评分。
错误、缺失和不支持都保留原始证据；PASS 仅涵盖已执行、已声明的检查，
不自动决定采用代码。多个指标/场景的 `assessment` 仍按其各项必要门分别报告。
联合评估可按事先声明的 profile 继承、覆盖或显式关闭主张；详见
[联合评估指南](joint-assessment.md)。

## 每次测量前的状态动作

`execution_state` 声明 process、object_state、cache、external_state、scope 和
是否为必要条件，按需提供受信任的 `setup`、`reset`、`verify`、`cleanup` argv。
平台在原有 oracle、预检及预热之后，正式计时工具调用之前按顺序运行
setup → reset → verify；cleanup 在该测量及相关后处理之后执行。每个动作的
stdout/stderr、执行事实和结果保存在所属 Run；成本列入 `budget`，不计入工具
目标时间，也不自动从时间中扣除。失败时停止并保留证据；cleanup 错误不会
掩盖已有的主要失败。

`verify` 必须向 stdout 写一个符合 `perflab schema state-verification --json`
的 JSON，包含 `schema_version: 1`、`status: PASS|FAIL|INCONCLUSIVE` 和非空
`description`，可附消费者定义的 `observations`。失败或不确定的验证中止该
正式测量。未提供 verifier 时，声明及成功动作仍不能证明状态；必需状态的
有效性无法通过。通过仅表示这个受信验证器在其描述的范围内通过。

`measurement_sample` 要求每个正式样本之前能执行动作；批量 Adapter 无法
在内部重复之间插入该边界，因此不支持此范围。`measurement_invocation` 在批量
工具上只能验证一次外层工具调用，不证明每个内部重复的状态。现有显式并发
A/B 与状态动作组合当前不支持；串行配对仍可使用。状态动作允许的外部文件、
逻辑和副作用由使用方拥有。将动作依赖的脚本/库作为显式构建 artifacts；
可变状态文件不是应保持不变的输入指纹。平台不自动清系统缓存、恢复未知快照
或推断不同进程之间的内存对象状态。

## 探索、校准、确认和验收

可选的 Study `workflow` 声明 `phase`（`exploration`、`calibration`、
`confirmation`、`acceptance`）、来源 Study ID 和选择理由。确认额外指定探索
来源中选定的 candidate Run；验收指定确认比较中的 candidate Run。所有 ID
必须来自实际返回值。配置、候选产物、输入、指标、策略及预算在阶段开始前
归档冻结；后续 Run 和比较通过原有 Study 委托执行，不复用来源样本。

- 探索记录平台内已执行或明确提供的候选；未提供的平台外尝试不被假定已知。
- 显式 A/A `calibration` 使用同程序、同输入、同状态和测量控制的 paired Study。
  它根据预声明的区间带报告结果，无法自动扣除偏差或证明没有环境漂移。
  仅声明执行状态、没有优化 Claim 的 A/A 可以有通过的状态检查和原统计结果，
  但 `claim_acceptance` 为 `INCONCLUSIVE`，因为它没有声明优化主张。
- 确认使用新 Run/样本，先冻结候选与判断规则；修改候选、主指标、门槛或停止
  规则后应开始新一轮确认。新 Run ID 和记录链不证明统计独立。
- 验收可引用确认及可选 A/A，保留原确认比较的结论；工程门通过、确认改善及
  使用方最终采纳是不同事情。需要泛化到新输入的主张还需相应独立输入证据。

`workflow.status=PASS` 只表示本阶段冻结、来源与执行来源检查通过，不能将它
解释为 A/A 通过、统计改善或自动采纳。原统计、`validity`、`claim_acceptance`
和各来源确认结果需要分别检查。失败记录及输入继续保留，不自动重试或挑选
成功子集。

## 本仓库独立合成示例

以下只构建小型合成目标；配置生成器不执行目标、probe 或构建。示例内部工作
计数是逻辑元素访问数，不是 CPU 硬件指令或物理内存流量。默认三配对区块及
阈值用于演示接口，不能据此宣称稳定收益；调整为适合目标的实验设计应提前
确定预算和策略。各 Run 的 oracle 会执行独立的小样例检查，预算无法推断它的
内部调用次数。

生成器会覆盖 `workloads/validity-*.json` 合成配置，但不会重写已保存的实验
记录或状态文件。修改示例规模和区块数时使用脚本参数，并在引用前一阶段时
保持相关目标参数一致；实际使用方配置应保存在平台仓库外并自行管理。

```bash
cmake --preset profile
cmake --build build/profile --target validity_workload
python3.11 scripts/make_validity_examples.py --blocks 3
bash scripts/install.sh
source scripts/env.sh
perflab schema claim --json
perflab schema execution-state --json
perflab schema state-verification --json
perflab schema workflow --json
perflab schema workflow-result --json
perflab schema validity --json
perflab budget --config workloads/validity-multiple.request.json --json
perflab plan --config workloads/validity-multiple.request.json --json
perflab run --config workloads/validity-multiple.request.json --json
perflab analyze RUN_ID --section validity --json
perflab analyze RUN_ID --section execution_state_evidence --json
```

该单 Run 在三个测量调用前各重置并验证合成状态文件，原始目标输出、状态动作
和阶段证据保留在 Run。它只能展示单版本的状态证据及样本，不能推断收益。
配置中的待写状态文件放在 `.perflab/validity-example/`，并行启动这个独立示例
会竞争同一文件；每个正式测量实验按项目规则串行执行。

预览并执行配对实验，再查看探索 Study：

```bash
perflab budget --kind study --config workloads/validity-exploration.study.json --json
perflab study --config workloads/validity-exploration.study.json --json
perflab analyze EXPLORATION_ID --kind study --section workflow --json
perflab report EXPLORATION_ID --kind study --format json
```

如果需要 A/A，先查看预算并显式运行生成的 `workloads/validity-aa.study.json`；
该配置两侧完全相同，不携带本次优化 Claim。用探索结果中 `runs` 的实际
candidate `run_id` 生成确认配置，然后再预览预算和执行：

```bash
python3.11 scripts/make_validity_examples.py --blocks 3 --exploration EXPLORATION_ID --selected-run CANDIDATE_RUN_ID
perflab budget --kind study --config workloads/validity-confirmation.study.json --json
perflab study --config workloads/validity-confirmation.study.json --json
perflab analyze CONFIRMATION_ID --kind study --section workflow --json
```

若已运行且要引用 A/A，生成时额外传入 `--calibration CALIBRATION_ID`；其能否
用于此确认由已声明和观察到的条件共同决定。最终验收选取确认结果中
comparison 的实际 candidate Run ID：

```bash
python3.11 scripts/make_validity_examples.py --blocks 3 --confirmation CONFIRMATION_ID --confirmed-run CONFIRMED_CANDIDATE_RUN_ID
perflab budget --kind study --config workloads/validity-acceptance.study.json --json
perflab study --config workloads/validity-acceptance.study.json --json
perflab report ACCEPTANCE_ID --kind study --format both --output results/validity-acceptance --json
```

两份报告来自同一分析视图。检查每项有效性、原统计结果、来源确认、必要资源
门与原始证据，不把工作流完成当作优化已获接受。真实使用方的配置、状态、输入、
Store 和报告保存在平台仓库外，消费工程需自行定义代表性负载及 oracle。
