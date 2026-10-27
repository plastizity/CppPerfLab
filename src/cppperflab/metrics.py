"""Metric semantics, evidence states and same-sample derivations; no execution."""
import math
import json
from copy import deepcopy
from pathlib import PurePosixPath

from .protocol import require

STATES = ('Measured', 'Estimated', 'Modeled', 'Unsupported', 'Missing', 'Invalid')
NUMERIC_STATES = STATES[:3]
# Only methods actually emitted by version-1 adapters are upgraded on read.
LEGACY_MEASURED = {'native-process-timing', 'elapsed-count', 'software-counter',
                   'hardware-counter', 'derived-paired-hardware-counters'}

DERIVATIONS = {
    'cpu.ipc': ('cpu.instructions', 'cpu.cycles', 1, 'instructions/cycle'),
    'cpu.cpi': ('cpu.cycles', 'cpu.instructions', 1, 'cycles/instruction'),
    'control.branch_mpki': ('cpu.branch_misses', 'cpu.instructions', 1000, 'miss/1000 instructions'),
    'control.branch_miss_rate': ('cpu.branch_misses', 'cpu.branches', 1, 'ratio'),
}


DME_DERIVATIONS = {
    'cpu.cache_miss_ratio': ('cpu.cache_misses', 'cpu.cache_references', 1, 'ratio'),
    'cpu.cache_mpki': ('cpu.cache_misses', 'cpu.instructions', 1000, 'miss/1000 instructions'),
    'cpu.instructions_per_work': ('cpu.instructions', 'work.items', 1, 'count/work item'),
    'cpu.cache_misses_per_work': ('cpu.cache_misses', 'work.items', 1, 'count/work item'),
}
ALL_DERIVATIONS = {**DERIVATIONS, **DME_DERIVATIONS}


def definition(name, domains, category, unit, formula, scope, direction='none', operands=()):
    return dict(metric_id=name, definition_version=1, name=name, domains=domains,
                categories=[category], unit=unit, mathematical_definition=formula,
                description=formula, measurement_scope=scope, expected_direction=direction,
                operands=list(operands), aggregation_rules='Keep independent repeats; ratios use paired operands, never means from different runs.',
                comparison_constraints=['same workload/input', 'same method/scope', 'same event semantics and tool version', 'valid evidence'],
                availability='requires actual capability probe and target collection',
                reliability='See method, coverage and original evidence; no causal claim.',
                raw_evidence_reference='run_id/sample index/evidence path and inventory hash')


DEFINITIONS = {
    'time.wall': definition('time.wall', ['D01','D02','D03','D04','D05','D07','D11','D12'], 'Outcome', 'seconds', 'end - start', 'whole target process including startup', 'lower'),
    'time.cpu': definition('time.cpu', ['D06','D07','D09'], 'Diagnostic', 'seconds', 'task CPU time', 'target process and inherited threads'),
    'time.iteration.wall': definition('time.iteration.wall', ['D03','D07','D12'], 'Outcome', 'seconds', 'elapsed / iterations', 'registered benchmark timing region', 'lower'),
    'time.iteration.cpu': definition('time.iteration.cpu', ['D03'], 'Diagnostic', 'seconds', 'CPU time / iterations', 'registered benchmark CPU timing region'),
    'memory.heap_peak_observed': definition('memory.heap_peak_observed', ['D04'], 'Outcome', 'bytes', 'max recorded mem_heap_B', 'Massif recorded heap snapshots; not RSS', 'lower'),
    'cpu.hotspots': definition('cpu.hotspots', ['D01','D06'], 'Diagnostic', '%', 'self sample period / total sample period * 100', 'on-CPU sampling; not exact duration'),
}
for name, domains in {
    'cpu.instructions':['D02','D03','D06'], 'cpu.cycles':['D06'],
    'cpu.branches':['D01'], 'cpu.branch_misses':['D01'],
    'cpu.cache_references':['D05'], 'cpu.cache_misses':['D05'],
    'os.page_faults':['D10'], 'os.context_switches':['D09'],
}.items():
    DEFINITIONS[name] = definition(name, domains, 'Diagnostic', 'count', 'sum of explicitly selected events', 'target process and inherited threads; event-specific')
# Linux HW_CACHE aliases identify requested event semantics, not a promise of
# universal microarchitectural event populations on every CPU/PMU.
from .perf_events import CACHE_ALIASES, descriptor_definition
for selector, descriptor in CACHE_ALIASES.items():
    name = descriptor['metric_id']
    domains = ['D05', 'D10'] if selector.startswith('dTLB-') else ['D05']
    DEFINITIONS[name] = definition(name, domains, 'Diagnostic', 'count',
        descriptor['description'], 'native target process and inherited threads; Linux PERF_TYPE_HW_CACHE alias')
    DEFINITIONS[name]['event_descriptor'] = descriptor_definition(selector)
    DEFINITIONS[name]['comparison_constraints'] += [
        'same CPU, PMU, kernel, native capture context, event selector and filter',
        'same resolved event semantics key; event population depends on CPU/kernel/PMU']
    DEFINITIONS[name]['reliability'] = ('Raw alias count from a supported native PMU; actual event population depends on CPU, kernel and PMU. '
        'LLC load misses do not represent all LLC misses or DRAM traffic.')

for name, (num, den, factor, unit) in DERIVATIONS.items():
    DEFINITIONS[name] = definition(name, ['D06'] if name.startswith('cpu.') else ['D01'],
                                   'Efficiency' if name.startswith('cpu.') else 'Diagnostic', unit,
                                   f'{num} / {den} * {factor}', 'same sample, event filters and scope', operands=(num,den))
for name, (num, den, factor, unit) in DME_DERIVATIONS.items():
    is_work = den == 'work.items'
    DEFINITIONS[name] = definition(name, ['D05', 'D06'], 'Efficiency' if is_work else 'Diagnostic', unit,
        f'{num} / {den} * {factor}', 'same target invocation; grouped hardware counters' +
        (' and completed target-reported work' if is_work else ''), operands=(num, den))
    DEFINITIONS[name]['comparison_constraints'] += [
        'same hardware event semantics, filters, CPU and capture window',
        'same-sample grouped PMU evidence and no counter scaling',
        '100% counter coverage and identical work definition/unit' if is_work else 'at least 99% grouped counter coverage']
    DEFINITIONS[name]['reliability'] = ('Estimated from grouped unscaled raw counts; generic cache events are CPU dependent. '
        'The cache-miss/reference ratio is not a probability and may exceed one.')
DEFINITIONS['work.items'] = definition('work.items', ['D02', 'D05', 'D06'], 'Diagnostic',
    'declared work unit', 'observed completed work reported by target for this invocation',
    'whole target invocation; completed business work')
DEFINITIONS['work.items']['comparison_constraints'] += ['same work definition, unit and completed-work semantics']

for name, formula in [('algorithm.growth_ratio','median(metric at N2) / median(metric at N1)'),
                      ('algorithm.doubling_ratio','median(metric at 2N) / median(metric at N)'),
                      ('algorithm.empirical_exponent','log(T2/T1) / log(N2/N1)')]:
    DEFINITIONS[name] = definition(name, ['D02'], 'Diagnostic', 'ratio', formula,
                                   'declared study axis; other workload features fixed')


# Native profile semantics remain separate from CPU hardware events.
from .valgrind_profiles import CACHE_EVENTS, CACHE_RATIOS, DHAT_FIELDS
DEFINITIONS['control.call_count']=definition('control.call_count',['D01'],'Diagnostic','count',
    'sum recorded Callgrind call-edge counts','whole instrumented target; runtime included; inlined calls excluded')
DEFINITIONS['callgrind.instructions']=definition('callgrind.instructions',['D01','D02'],'Diagnostic','count',
    'Callgrind totals Ir','instrumented guest instruction references; not native retired PMU instructions')
for name,field in DHAT_FIELDS.items():
    DEFINITIONS[name]=definition(name,['D04','D02'],'Outcome' if 'heap' in name else 'Diagnostic',
        'count' if field=='tbk' else 'bytes','sum DHAT '+field,
        'tracked heap over whole process; runtime included; global peak uses simultaneous gb','lower' if 'heap' in name else 'none')
for name,unit,formula in [
    ('memory.allocations_per_operation','allocations/operation','sum tbk / explicit operation_count'),
    ('memory.allocated_bytes_per_operation','bytes/operation','sum tb / explicit operation_count')]:
    DEFINITIONS[name]=definition(name,['D04'],'Diagnostic',unit,formula,'whole target; operation_count supplied explicitly')
for suffix in CACHE_EVENTS.values():
    name='cachegrind.'+suffix
    DEFINITIONS[name]=definition(name,['D01'] if 'branch' in suffix else ['D05'],'Diagnostic','count',
        'Cachegrind native event summary','simulated cache/branch model; never a PMU event')
for suffix,(nums,dens,factor) in CACHE_RATIOS.items():
    name='cachegrind.'+suffix
    DEFINITIONS[name]=definition(name,['D01'] if 'branch' in suffix else ['D05'],'Diagnostic',
        'miss/1000 instructions' if suffix.endswith('mpki') else 'ratio',
        f"sum({','.join(nums)}) / sum({','.join(dens)}) * {factor}",'same Cachegrind model and event summary')
for name,category,unit,formula,scope,direction,operands in [
    ('kernel.useful_flops','Diagnostic','FLOP','declared algorithm FLOPs per iteration','target-defined useful work, not FP hardware events','none',()),
    ('kernel.logical_input_bytes','Diagnostic','bytes','logical input bytes per iteration','logical input, not physical traffic','none',()),
    ('algorithm.operations','Diagnostic','count','declared operations per iteration','benchmark target explicit per-iteration count','none',()),
    ('kernel.useful_flop_rate','Efficiency','FLOP/s','useful_flops / iteration wall seconds','same benchmark repetition','higher',('kernel.useful_flops','time.iteration.wall')),
    ('workload.throughput','Outcome','operation/s','operations / iteration wall seconds','same benchmark repetition','higher',('algorithm.operations','time.iteration.wall')),
    ('kernel.logical_arithmetic_intensity','Diagnostic','FLOP/byte','useful_flops / logical_input_bytes','logical traffic model; not DRAM Roofline','none',('kernel.useful_flops','kernel.logical_input_bytes')),
]:
    DEFINITIONS[name]=definition(name,['D02','D03'] if name=='algorithm.operations' else ['D03'],category,unit,formula,scope,direction,operands)


for name,unit,formula,category,direction in [
    ('memory.copied_bytes','bytes','declared bytes copied per iteration','Diagnostic','none'),
    ('pipeline.full_traversals','count','declared full traversals per iteration','Diagnostic','none'),
    ('pipeline.rebuilds','count','declared structure rebuilds per iteration','Diagnostic','none'),
    ('pipeline.reuses','count','declared successful reuses per iteration','Diagnostic','none'),
    ('pipeline.stage_wall','seconds','inclusive stage wall seconds per iteration','Outcome','lower'),
    ('pipeline.stage_fraction','ratio','stage wall seconds / enclosing iteration wall seconds','Diagnostic','none'),
]:
    DEFINITIONS[name]=definition(name,['D12'],category,unit,formula,'explicit target-defined iteration/stage; no implicit pipeline reconstruction',direction)
for name,formula in [('parallel.speedup','median T(1) / median T(p)'),
                     ('parallel.efficiency','speedup(p) / p')]:
    DEFINITIONS[name]=definition(name,['D07'],'Efficiency','ratio',formula,'strong scaling; fixed workload; declared worker count','higher')


for name,category,unit,formula,direction in [
    ('solver.setup_time','Diagnostic','seconds','target setup end - start','none'),
    ('solver.solve_time','Diagnostic','seconds','target solve end - start','none'),
    ('solver.total_time','Outcome','seconds','target total setup/solve interval; external validator excluded','lower'),
    ('solver.iterations','Diagnostic','count','completed solver iterations','none'),
    ('numerical.residual','Diagnostic','problem units or ratio','independently recomputed declared L2 residual norm','none'),
    ('numerical.absolute_error','Diagnostic','problem units','independently recomputed L2 solution error','none'),
    ('numerical.relative_error','Diagnostic','ratio','L2 solution error / L2 reference solution norm','none'),
]:
    DEFINITIONS[name]=definition(name,['D14'],category,unit,formula,'one solve with explicit independent validator and numerical gates',direction)


for name,unit,formula,category,direction in [
    ('phase.inclusive_time','seconds','sum of reported call wall durations at one stage path','Outcome','lower'),
    ('phase.self_time','seconds','reported self wall total, or parent minus complete child intervals; aggregate-only child subtraction remains Estimated','Outcome','lower'),
    ('phase.calls','count','number of reported calls at one stage path','Diagnostic','none'),
    ('phase.work','declared counter unit','sum of same-name same-unit target counters at one stage path','Diagnostic','none'),
    ('phase.covered_wall','seconds','union of top-level intervals across tracks','Diagnostic','none'),
    ('phase.uncovered_wall','seconds','root wall duration minus covered wall union','Diagnostic','none'),
    ('phase.root_wall','seconds','declared root end - start; not necessarily whole process','Outcome','lower'),
]:
    DEFINITIONS[name]=definition(name,['D12','D07'],category,unit,formula,'explicit target phase report; clock/child coverage and work units declared',direction)

for name,unit,formula in [
    ('phase.application_wait_wall','seconds','per-track union duration of explicit application_wait annotations'),
    ('phase.application_work_wall','seconds','per-track union duration of explicit application_work annotations'),
    ('phase.application_unclassified_wall','seconds','observed root interval union minus explicitly classified interval unions on one track'),
    ('phase.application_observed_wall','seconds','per-track union duration of recorded root intervals'),
    ('phase.application_wait_fraction','ratio','recorded application wait union / observed root union on the same track'),
]:
    DEFINITIONS[name]=definition(name,['D07','D08','D09'],'Diagnostic',unit,formula,
        'explicit caller annotations on one producer track; wall scopes include scheduling and API return overhead; not OS running/off-CPU time')
    DEFINITIONS[name]['comparison_constraints']+=['same producer track and annotation classification contract',
        'missing work/wait markers are not zero; lower wait fraction alone is not optimization acceptance']


def catalog(registry, domain=None, metric_id=None):
    require(domain is None or domain in {d for v in DEFINITIONS.values() for d in v['domains']}, 'Unknown or unimplemented domain')
    require(metric_id is None or metric_id in DEFINITIONS, 'Unknown metric')
    result = []
    for name, spec in DEFINITIONS.items():
        if (domain and domain not in spec['domains']) or (metric_id and name != metric_id):
            continue
        operands = spec['operands'] or [name]
        from .solver import METRIC_IDS
        from .phases import METRIC_IDS as PHASE_METRICS
        phase_metric=name in PHASE_METRICS
        solver_metric=name in METRIC_IDS
        providers = [key for key, adapter in registry.items() if (not adapter.batch if solver_metric or phase_metric else set(operands[:1] if name in DME_DERIVATIONS and operands[-1] == 'work.items' else operands) <= set(adapter.metrics))]
        if name in DME_DERIVATIONS:
            collection_method = 'same-invocation grouped PMU counters; explicit target work report also required' if operands[-1] == 'work.items' else 'same-invocation grouped PMU counters'
        elif name == 'work.items':
            collection_method = 'explicit target invocation work report'
        else:
            collection_method = 'explicit per-execution interval or declared summary phase report' if phase_metric else 'explicit post-sample solver validation' if solver_metric else 'same-sample derivation' if spec['operands'] else ('study' if name.startswith('parallel.') or name in ('algorithm.growth_ratio','algorithm.doubling_ratio','algorithm.empirical_exponent') else 'adapter')
        result.append({**spec, 'source_capability': operands,
                       'providers': providers, 'collection_method': collection_method})
    response = {'schema_version': 1, 'metrics': result,
                'note': 'Definitions and provider declarations are not availability claims; use tools probe and plan.'}
    if metric_id is None and (domain is None or domain in ('D05', 'D10', 'D12')):
        response['capability_gaps'] = [{
            'capability_id': 'memory_controller_traffic',
            'domains': ['D05', 'D10', 'D12'],
            'collection_status': 'Unsupported',
            'reason': 'collector_not_implemented',
            'scope': 'shared controller/socket/system; not process',
            'discovery_command': 'perflab tools probe --capability memory-controller-traffic --json',
            'requirements': ['native PMU visibility', 'documented units and scale',
                'actual permission probe', 'all controllers, topology, window and background scope validation'],
            'interpretation': 'Current platform collection capability only; does not describe any historical run or physical hardware.'
        }]
    return response


def convert(value, source, target):
    units = {'seconds':('time',1), 's':('time',1), 'ns':('time',1e-9),
             'bytes':('bytes',1), 'byte':('bytes',1), 'KiB':('bytes',1024), 'kB':('bytes',1000),
             'ratio':('ratio',1), '%':('ratio',.01), 'count':('count',1)}
    require(type(value) in (float,int) and math.isfinite(value), 'Conversion requires a finite number')
    require(source in units and target in units and units[source][0] == units[target][0], 'Incompatible units')
    return value * units[source][1] / units[target][1]


def evidence_state(item):
    if 'evidence_state' in item:
        require(item.get('evidence_version',1)==1, 'Unsupported evidence version')
        state = item['evidence_state']
        require(state in STATES, 'Unknown evidence state')
        if state in NUMERIC_STATES:
            require(type(item.get('value')) in (int,float) and math.isfinite(item['value']), 'Numeric evidence requires a finite value')
        else:
            require(item.get('value') is None and bool(item.get('reason')), 'Unavailable evidence requires null value and reason')
        return state
    method = item.get('method')
    if method in LEGACY_MEASURED or method in {'instrumented-snapshot', 'in-process-benchmark'}:
        return 'Measured'
    # Unknown legacy methods cannot be silently promoted to measurements.
    return 'Invalid'


def observation(name, state, *, value=None, reason=None, **metadata):
    result = dict(name=name, evidence_state=state, evidence_version=1, value=value, **metadata)
    if reason:
        result['reason'] = reason
    evidence_state(result)
    return result


def derive_sample(sample):
    out = []
    for name, (numerator, denominator, factor, unit) in DERIVATIONS.items():
        operands = [[m for m in sample['metrics'] if m['name'] == key] for key in (numerator,denominator)]
        state, reason = 'Measured', None
        if any(len(v) == 0 for v in operands):
            state, reason = 'Missing', 'required operand not collected in this sample'
        elif any(len(v) != 1 for v in operands):
            state, reason = 'Invalid', 'ambiguous duplicate operands'
        else:
            a, b = (v[0] for v in operands)
            states = [evidence_state(m) for m in (a,b)]
            if any(s not in NUMERIC_STATES for s in states):
                state = 'Invalid' if 'Invalid' in states else ('Unsupported' if 'Unsupported' in states else 'Missing')
                reason = 'operand unavailable or invalid'
            elif (a['unit'] != 'count' or b['unit'] != 'count' or a['scope'] != b['scope']
                  or a['method'] != 'hardware-counter' or b['method'] != 'hardware-counter'
                  or any(a.get(k) != b.get(k) for k in ('tool','tool_version'))
                  or a.get('event','').partition(':')[2] != b.get('event','').partition(':')[2]
                  or any(not 99 <= m.get('running_percent',0) <= 100 for m in (a,b))
                  or a['value'] < 0 or b['value'] <= 0
                  or (name == 'control.branch_miss_rate' and a['value'] > b['value'])):
                state, reason = 'Invalid', 'incompatible operands, incomplete coverage or invalid denominator/count'
            elif 'Modeled' in states:
                state = 'Modeled'
            elif 'Estimated' in states:
                state = 'Estimated'
        sources = [deepcopy(v[0]) for v in operands if len(v)==1]
        value = sources[0]['value']/sources[1]['value']*factor if state in NUMERIC_STATES else None
        out.append(observation(name,state,value=value,reason=reason,unit=unit,
                               method='derived-paired-hardware-counters', scope=sources[0]['scope'] if sources else 'unavailable',
                               evidence=sources[0].get('evidence','') if sources else '', sources=sources,
                               sample_index=sample.get('index'), definition_version=1, derivation=DEFINITIONS[name]['mathematical_definition']))
    return out


def _grouped_counter_error(item, minimum_coverage):
    """Validate actual grouped PMU provenance, never infer it from event names."""
    from .adapters import EVENTS
    required = ('scope', 'tool', 'tool_version', 'event', 'raw_event', 'evidence',
                'collection_evidence', 'capture_scope', 'capture_window', 'counter_scaling',
                'event_group', 'group_events', 'event_group_status', 'counter_runtime_ns', 'running_percent')
    if any(item.get(key) is None or item.get(key) == '' for key in required):
        return 'Missing', 'grouped counter provenance is incomplete'
    if item['event_group_status'] == 'UNSUPPORTED':
        return 'Unsupported', 'hardware event group is unsupported'
    group = item['group_events']
    event = item['event'].partition(':')[0]
    if (item['method'] != 'hardware-counter' or item['unit'] != 'count'
            or item['event'] != item['raw_event'] or event not in EVENTS
            or EVENTS[event][0] != item['name'] or item['capture_scope'] != 'target_process_and_inherited_threads'
            or item['capture_window'] != 'whole-target-invocation' or item['counter_scaling'] != 'none'
            or not isinstance(group, list) or len(set(group)) < 2 or event not in group
            or item['event_group_status'] != 'PASS' or item['counter_runtime_ns'] <= 0
            or not minimum_coverage <= item['running_percent'] <= 100 or item['value'] < 0):
        return 'Invalid', 'grouped counter provenance, coverage or count is incompatible'
    return None


def derive_grouped_sample(sample):
    """DME projections use only operands recorded in this exact invocation."""
    out = []
    for name, (numerator, denominator, factor, unit) in DME_DERIVATIONS.items():
        operands = [[m for m in sample['metrics'] if m['name'] == key]
                    for key in (numerator, denominator)]
        sources = [deepcopy(v[0]) for v in operands if len(v) == 1]
        state, reason = 'Estimated', None
        is_work = denominator == 'work.items'
        if any(not values for values in operands):
            state, reason = 'Missing', 'required operand not collected in this sample'
        elif any(len(values) != 1 for values in operands):
            state, reason = 'Invalid', 'ambiguous duplicate operands'
        else:
            a, b = sources
            states = [evidence_state(m) for m in sources]
            if any(st != 'Measured' for st in states):
                state = 'Invalid' if any(st in ('Invalid', 'Estimated', 'Modeled') for st in states) else ('Unsupported' if 'Unsupported' in states else 'Missing')
                reason = 'operands must be actual measured evidence'
            else:
                errors = [_grouped_counter_error(a, 100 if is_work else 99)]
                if not is_work:
                    errors.append(_grouped_counter_error(b, 99))
                error = next((x for x in errors if x and x[0] == 'Invalid'), None) or next((x for x in errors if x), None)
                if error:
                    state, reason = error
                elif is_work:
                    if any(not b.get(key) for key in ('work_definition_id', 'work_unit', 'counter_contract', 'evidence')):
                        state, reason = 'Missing', 'target work provenance is incomplete'
                    else:
                        try:
                            contract = json.loads(b['counter_contract'])
                        except (ValueError, TypeError):
                            contract = None
                        if (b['method'] != 'target-reported-invocation-work' or b['scope'] != 'target invocation; completed business work'
                                or b.get('capture_scope') != a['capture_scope'] or b.get('capture_window') != a['capture_window']
                                or PurePosixPath(b['evidence']).parent != PurePosixPath(a['evidence']).parent
                                or PurePosixPath(a['collection_evidence']).parent != PurePosixPath(a['evidence']).parent
                                or b['unit'] != b['work_unit'] or b.get('count_kind') != 'observed' or b.get('completed') is not True
                                or type(b['value']) is not int or b['value'] < 0
                                or not isinstance(contract, dict) or contract.get('definition_id') != b['work_definition_id']
                                or contract.get('unit') != b['work_unit'] or contract.get('scope') != 'target-invocation'
                                or contract.get('count_kind') != 'observed' or contract.get('completed') is not True):
                            state, reason = 'Invalid', 'target work definition, completion or capture scope is incompatible'
                        unit = 'count/' + b['work_unit']
                else:
                    shared = ('scope', 'tool', 'tool_version', 'evidence', 'collection_evidence',
                              'capture_scope', 'capture_window', 'counter_scaling', 'event_group',
                              'group_events', 'counter_runtime_ns')
                    if (any(a.get(key) != b.get(key) for key in shared)
                            or a['event'].partition(':')[2] != b['event'].partition(':')[2]
                            or any(m['event'].partition(':')[0] not in a['group_events'] for m in (a, b))):
                        state, reason = 'Invalid', 'counter events do not share the same group, window, filter and evidence'
                if state == 'Estimated' and b['value'] == 0:
                    state, reason = 'Missing', 'zero denominator does not define a ratio'
        limitations = ['Derived from unscaled grouped hardware counters; no causal conclusion.']
        if name == 'cpu.cache_miss_ratio':
            limitations.append('Generic cache events are CPU dependent; this ratio is not a probability and can exceed one.')
        if is_work:
            limitations.append('100% is the reported counter coverage and may be rounded; work is defined by the target.')
        extra = {}
        if is_work and len(operands[1]) == 1:
            work = operands[1][0]
            extra = {key: work[key] for key in ('work_definition_id', 'work_unit', 'counter_contract') if key in work}
        if sources:
            for key in ('tool', 'tool_version', 'capture_scope', 'capture_window', 'collection_evidence'):
                if key in sources[0]:
                    extra[key] = sources[0][key]
        out.append(observation(name, state, value=sources[0]['value'] / sources[1]['value'] * factor if state == 'Estimated' else None,
                               reason=reason, unit=unit, method='derived-grouped-hardware-counters',
                               scope=sources[0]['scope'] if sources else 'unavailable',
                               evidence=sources[0].get('evidence', '') if sources else '', sources=sources,
                               sample_index=sample.get('index'), definition_version=1,
                               derivation=DEFINITIONS[name]['mathematical_definition'], limitations=limitations, **extra))
    return out


def derive_all_sample(sample):
    return derive_sample(sample) + derive_grouped_sample(sample)


def metric_samples(run, name):
    return [m for s in run['samples'] for m in
            (derive_all_sample(s) if name in ALL_DERIVATIONS else s['metrics']) if m['name']==name]


def run_observations(run):
    """Read projection only: never rewrite an archived legacy record."""
    result = []
    for sample in run['samples']:
        for raw in sample['metrics']:
            state = evidence_state(raw)
            item = {**deepcopy(raw), 'evidence_state':state, 'evidence_version':1,
                    'sample_index':sample.get('index'), 'run_id':run['run_id']}
            if state not in NUMERIC_STATES:
                item['value'] = None
                item.setdefault('reason','Legacy method has no verified evidence semantics')
            result.append(item)
        result.extend({**m, 'run_id':run['run_id']} for m in derive_all_sample(sample))
    result.extend(run.get('unavailable_observations',[]))
    return result
