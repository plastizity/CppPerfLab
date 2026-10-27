"""Dimension-wise evidence summaries; never collapse metrics into a score.

This module interprets saved summaries and classifications. It does not collect
events, prove causes or convert lower instruction counts into elapsed speedup.
"""
from .lab import summarize
from .protocol import metric


DIMENSIONS = {
    'work': {'title': '计算工作量', 'metrics': [
        'work.items', 'cpu.instructions', 'algorithm.operations', 'algorithm.evaluations',
        'algorithm.iterations', 'algorithm.elements', 'solver.iterations', 'phase.work', 'phase.calls', 'callgrind.instructions', 'kernel.useful_flops']},
    'resources': {'title': '内存与资源', 'metrics': [
        'memory.heap_peak_observed', 'memory.rss_peak', 'memory.allocations',
        'memory.allocated_bytes', 'memory.reads', 'memory.writes', 'memory.copied_bytes',
        'memory.heap_peak_live', 'memory.heap_end_live', 'memory.allocations_per_operation',
        'memory.allocated_bytes_per_operation', 'kernel.logical_input_bytes']},
    'execution': {'title': '执行行为', 'metrics': [
        'cpu.cycles', 'cpu.cache_references', 'cpu.cache_misses', 'cpu.cache_miss_ratio',
        'cpu.cache_mpki', 'cpu.cache_misses_per_work', 'cpu.branches',
        'cpu.branch_misses', 'pipeline.full_traversals', 'pipeline.rebuilds', 'pipeline.reuses', 'cpu.tlb_misses', 'os.page_faults', 'os.context_switches', 'control.call_count']},
    'instruction_quality': {'title': '指令执行质量', 'metrics': [
        'cpu.ipc', 'cpu.instructions_per_work', 'simd.vector_instructions', 'simd.scalar_instructions',
        'simd.lane_utilization', 'simd.operations_per_instruction',
        'compiler.vectorized_loops']},
    'timing': {'title': '耗时', 'metrics': [
        'time.wall', 'time.cpu', 'time.iteration.wall', 'time.iteration.cpu', 'pipeline.stage_wall', 'solver.setup_time', 'solver.solve_time', 'solver.total_time','phase.inclusive_time','phase.self_time','phase.covered_wall','phase.uncovered_wall','phase.root_wall']},
    'correctness': {'title': '正确性与数值质量', 'metrics': [
        'numerical.absolute_error', 'numerical.relative_error', 'numerical.residual']},
}

METRIC_DIMENSION = {name: key for key, value in DIMENSIONS.items() for name in value['metrics']}


def dimension(metric_name):
    # Unknown metrics remain visible without inventing an optimization objective.
    return METRIC_DIMENSION.get(metric_name, 'unclassified')


def data_movement_projection(runs, comparisons=(), members=()):
    """Project saved observations, without combining runs or inferring causes."""
    from .metrics import run_observations, NUMERIC_STATES, DEFINITIONS
    columns = {key: {'observations': [], 'comparisons': [], 'unavailable': []}
               for key in ('outcome', 'work', 'data_movement')}
    outcome = {'time.wall', 'time.iteration.wall', 'solver.total_time', 'pipeline.stage_wall',
               'phase.inclusive_time', 'phase.self_time', 'workload.throughput'}
    work = {'work.items', 'algorithm.operations', 'algorithm.iterations', 'algorithm.evaluations',
            'algorithm.elements', 'solver.iterations', 'phase.work', 'phase.calls',
            'cpu.instructions', 'kernel.useful_flops', 'cpu.instructions_per_work'}
    movement = {'memory.copied_bytes', 'kernel.logical_input_bytes', 'pipeline.full_traversals',
                'memory.reads', 'memory.writes', 'cpu.cache_references', 'cpu.cache_misses',
                'cpu.cache_miss_ratio', 'cpu.cache_mpki', 'cpu.cache_misses_per_work'}
    cache_model = {'cachegrind.data_reads', 'cachegrind.data_writes', 'cachegrind.d1_read_misses',
                   'cachegrind.d1_write_misses', 'cachegrind.ll_read_misses',
                   'cachegrind.ll_write_misses', 'cachegrind.d1_miss_rate', 'cachegrind.ll_data_mpki'}
    def category(name):
        if name in outcome: return 'outcome'
        if name in work: return 'work'
        if name in movement or name in cache_model: return 'data_movement'
        return None
    member_by_id = {row['run_id']: {k: row[k] for k in
                    ('case_id','sample_id','profile_id','variant','block_id','slot','wave_id') if k in row}
                    for row in members}
    for run in runs:
        rid = run['run_id']
        for item in run_observations(run):
            key = category(item['name'])
            if key is None: continue
            row = dict(item)
            row['run_id'] = rid
            row['source_tool'] = run.get('tool')
            row['membership'] = member_by_id.get(rid, {})
            row['evidence_id'] = rid + ':' + item['evidence'] if item.get('evidence') else None
            sources = row.get('sources')
            if isinstance(sources, list):
                for source in sources:
                    if source.get('evidence'): source['evidence_id'] = rid + ':' + source['evidence']
            definition = DEFINITIONS.get(item['name'], {})
            row['denominator'] = (sources[1] if isinstance(sources, list) and len(sources) == 2 else
                                  {'scope': item.get('scope', 'unknown'),
                                   'definition': definition.get('mathematical_definition', 'not declared')})
            (columns[key]['observations'] if item['evidence_state'] in NUMERIC_STATES
             else columns[key]['unavailable']).append(row)
    for item in comparisons:
        key = category(item['metric'])
        if key is None: continue
        row = {field: item[field] for field in ('comparison_id','baseline_id','candidate_id',
               'baseline_run_ids','candidate_run_ids','case_id','profile_id','assessment_role',
               'metric','metric_scope','unit','comparable','classification','sufficient_samples',
               'stable_samples','baseline_median','candidate_median','change_fraction',
               'bootstrap_95_percent_speedup','block_pairs','validity','claim_acceptance') if field in item}
        classification = item.get('classification')
        row['observed_change'] = ('observed_reduction' if classification == 'observed_improvement' else
                                  'observed_increase' if classification == 'observed_regression' else 'inconclusive') \
            if item.get('comparable') and item.get('sufficient_samples') and item.get('stable_samples') else 'inconclusive'
        columns[key]['comparisons'].append(row)
    unknowns = [{'column': key, 'evidence_state': 'Missing',
                 'reason': 'No numeric observation is available for this column; see per-metric evidence states.'}
                for key, value in columns.items() if not value['observations']]
    return {'schema_version': 1, 'columns': columns, 'unknowns': unknowns, 'possible_explanations': [],
            'limitations': ['Rows retain their original scope, state and per-sample evidence; they are not aggregated across runs.',
                            'Reported work and logical bytes are target declarations, not physical DRAM traffic.',
                            'Instrumented-run time is not a native timing baseline; inspect each source tool.',
                            'Generic CPU cache events are not necessarily LLC or DRAM; Cachegrind is a model.',
                            'No causal attribution, composite score, or independent benefit follows from these observations.',
                            'A no-clear-change comparison does not establish equality.']}


def comparison_finding(result):
    from .comparison import normalized_cost, decisions
    key = dimension(result['metric'])
    finding = {'kind': 'statistical_inference', 'dimension': key, 'metric': result['metric'],
               'baseline_id': result['baseline_id'], 'candidate_id': result['candidate_id'],
               'classification': result.get('classification'), 'comparable': result['comparable'],
               'normalized_cost': normalized_cost(result), 'decisions':decisions(result), 'acceptance':result.get('acceptance')}
    for field in ('validity','claim_acceptance'):
        if field in result:finding[field]=result[field]
    finding.update({k:result[k] for k in ('comparison_id','metric_scope','case_id','profile_id','assessment_role','assessment_gate') if k in result})
    if not result['comparable']:
        finding.update(outcome='incomparable', message='测量条件或指标语义不兼容，未形成变化结论。',
                       error=result['error'])
        return finding
    finding.update(change_fraction=result['change_fraction'],
                   baseline_candidate_ratio=result['speedup'],
                   bootstrap_95_percent_ratio=result['bootstrap_95_percent_speedup'],
                   coefficient_of_variation=result['coefficient_of_variation'],
                   policy=result['policy'])
    classification = result['classification']
    if classification == 'inconclusive':
        finding.update(outcome='inconclusive', message='样本数量或波动不满足当前比较政策。')
    elif classification == 'no_clear_change':
        finding.update(outcome='no_clear_change', message='未观察到满足当前政策的明确变化。')
    else:
        lower = classification == 'observed_improvement'
        if key == 'work':
            finding.update(outcome='observed_work_reduction' if lower else 'observed_work_increase',
                           message='观察到计算工作量降低；独立于耗时和指令执行质量结论。' if lower else '观察到计算工作量增加。')
        elif key == 'resources':
            finding.update(outcome='observed_resource_reduction' if lower else 'observed_resource_increase',
                           message='观察到该资源指标降低。' if lower else '观察到该资源指标增加。')
        elif key == 'timing':
            finding.update(outcome='observed_time_reduction' if lower else 'observed_time_increase',
                           message='观察到该计时范围内耗时降低。' if lower else '观察到该计时范围内耗时增加。')
        else:
            finding.update(outcome='observed_value_decrease' if lower else 'observed_value_increase',
                           message='观察到指标数值变化；需结合指标含义判断，不自动判定整体收益。')
    validity=result.get('validity')
    if validity is not None and validity['status'] not in ('PASS','NOT_CONFIGURED'):
        finding['message']+=' 已声明的必要有效性条件未通过，不能作为有效主张收益。'
    if validity is not None and validity.get('claim_kind')=='mechanism_hypothesis':
        finding['message']+=' 性能原因仍是待验证假设。'
    return finding


def derive_ipc(run):
    """Only paired, full-coverage counters from the same sample and scope."""
    from .metrics import metric_samples
    samples = [{'index': m['sample_index'], 'metrics':[m]} for m in metric_samples(run,'cpu.ipc')
               if m['evidence_state']=='Measured']
    if not samples:
        return None
    return {'run_id': run['run_id'], 'summary': summarize(samples)[0],
            'samples': [{'index': s['index'], 'value': s['metrics'][0]['value'],
                         'evidence_id': run['run_id']+':'+s['metrics'][0]['evidence'],
                         'sources': s['metrics'][0]['sources']} for s in samples],
            'derived': True,
            'limitations': ['IPC is an execution-efficiency observation; it does not establish SIMD utilization or elapsed speedup.',
                            'Only valid same-sample counter pairs contribute; no cross-run ratio is constructed.']}


def summarize_performance(runs, comparisons=(), growth=()):
    dimensions = {key: {'id': key, 'title': spec['title'], 'coverage': 'not_collected',
                        'observations': [], 'comparisons': [], 'missing_metrics': list(spec['metrics'])}
                  for key, spec in DIMENSIONS.items()}
    dimensions['unclassified'] = {'id': 'unclassified', 'title': '未分类指标', 'coverage': 'not_collected',
                                  'observations': [], 'comparisons': [], 'missing_metrics': []}
    derived = []
    for run in runs:
        for summary in run['summary']:
            key = dimension(summary['name'])
            references = sorted({run['run_id']+':'+m['evidence'] for s in run['samples'] for m in s['metrics']
                                 if all(m[k] == summary[k] for k in ('name', 'unit', 'method', 'scope'))})
            dimensions[key]['observations'].append({'kind': 'observation', 'run_id': run['run_id'],
                                                     'run_status': run['status'], 'metric': summary,
                                                     'evidence_ids': references})
            if summary['name'] in dimensions[key]['missing_metrics']:
                dimensions[key]['missing_metrics'].remove(summary['name'])
        dimensions['correctness']['observations'].append({
            'kind': 'observation', 'run_id': run['run_id'], 'correctness': run['correctness'],
            'meaning': 'Declared oracle result and coverage only; no numerical error curve is inferred.'})
        ipc = derive_ipc(run)
        if ipc is not None:
            derived.append(ipc)
            dimensions['instruction_quality']['observations'].append({
                'kind': 'derived_observation', 'run_id': run['run_id'], 'metric': ipc['summary'],
                'evidence_ids': sorted({s['evidence_id'] for s in ipc['samples']}), 'limitations': ipc['limitations']})
            if 'cpu.ipc' in dimensions['instruction_quality']['missing_metrics']:
                dimensions['instruction_quality']['missing_metrics'].remove('cpu.ipc')
    findings = []
    for comparison in comparisons:
        finding = comparison_finding(comparison)
        findings.append(finding)
        dimensions[finding['dimension']]['comparisons'].append(finding)
        if comparison['comparable'] and comparison['metric'] in dimensions[finding['dimension']]['missing_metrics']:
            dimensions[finding['dimension']]['missing_metrics'].remove(comparison['metric'])
    for item in dimensions.values():
        if item['observations'] or any(c['comparable'] for c in item['comparisons']):
            item['coverage'] = 'partial' if item['missing_metrics'] else 'collected'
    for fitted in growth:
        findings.append({'kind': 'statistical_inference', 'dimension': dimension(fitted['points'][0]['name']) if fitted['points'] else 'unclassified',
                         'variant': fitted['variant'], 'outcome': 'empirical_growth_only',
                         'message': '规模增长拟合只描述已测范围，不证明 Big-O。',
                         'models': fitted.get('models', []), 'limitations': fitted['limitations']})
    return {'schema_version': 1, 'dimensions': list(dimensions.values()), 'findings': findings,
            'derived_metrics': derived, 'overall_score': None, 'optimization_accepted': False,
            'limitations': [
                'Instruction work, instruction quality, resource use and timing are independent conclusions; no composite score is computed.',
                'Missing metrics are not zero and do not imply an available collector.',
                'SIMD presence or vector instruction count alone does not prove efficient lane use or faster execution.',
                'Grouped runs remain separate observations; units, scopes and tool modes are not merged.',
                'A passed oracle is limited to its declared coverage; it is not a numerical proof.']}
