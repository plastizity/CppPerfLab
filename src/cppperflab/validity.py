"""Evaluate explicit claim conditions using existing run evidence.

The projection does not collect data, estimate performance, or prove that a
consumer's declarations and validator cover every property of a workload.
"""
import math

from .metrics import ALL_DERIVATIONS, derive_all_sample, evidence_state


LIMITATIONS = [
    'Consumer declarations and oracle coverage are not proof of scientific equivalence or completeness.',
    'Only explicitly checked conditions contribute; a performance change does not establish its cause.',
    'A passed condition does not authorize production adoption or prove independent samples.',
]


def _row(id, source, required, status, reason, evidence=(), *, origin='platform', description=None):
    result = dict(id=id, origin=origin, source=source, required=required,
                  status=status, evidence_refs=list(dict.fromkeys(evidence)), reason=reason)
    if description is not None:
        result['description'] = description
    return result


def _aggregate(rows):
    from .comparison import combine_gates
    return combine_gates([row['status'] for row in rows if row['required']])


def _ref(run, path):
    return f"{run['run_id']}:{path}" if path else None


def _refs(run, paths):
    return [value for path in paths if (value := _ref(run, path)) is not None]


def _missing_state(run, metric_name):
    states = [row['evidence_state'] for row in run.get('unavailable_observations', [])
              if row.get('name') == metric_name]
    return 'UNSUPPORTED' if 'Unsupported' in states else 'INCONCLUSIVE'


def _measurements(run, name, scope):
    """Return one measured, semantically uniform value per sample or a reason."""
    samples = run['samples']
    if not samples:
        status = _missing_state(run, name)
        reason = ('The requested metric was explicitly reported Unsupported.' if status == 'UNSUPPORTED'
                  else 'No formal samples were recorded.')
        return status, [], [], None, reason
    values, refs, meanings = [], [], set()
    for sample in samples:
        metrics = derive_all_sample(sample) if name in ALL_DERIVATIONS else sample['metrics']
        matched = [metric for metric in metrics if metric['name'] == name and
                   (scope is None or metric.get('scope') == scope)]
        if len(matched) != 1:
            state = _missing_state(run, name) if not matched else 'INCONCLUSIVE'
            return state, [], refs, None, 'Metric is missing or ambiguous in a formal sample.'
        metric = matched[0]
        state = evidence_state(metric)
        if state != 'Measured':
            return ('UNSUPPORTED' if state == 'Unsupported' else 'INCONCLUSIVE'), [], refs, None, \
                f'Metric evidence is {state}, not Measured.'
        from .perf_events import descriptor_problem
        if (problem := descriptor_problem(metric)) is not None:
            return 'INCONCLUSIVE', [], refs, None, problem
        value = metric.get('value')
        if type(value) not in (int, float) or not math.isfinite(value):
            return 'INCONCLUSIVE', [], refs, None, 'Measured metric has no finite value.'
        coverage = metric.get('running_percent')
        if ((metric['method'] == 'hardware-counter' and coverage is None)
                or (coverage is not None and not 99 <= coverage <= 100)):
            return 'INCONCLUSIVE', [], refs, None, 'Counter coverage is insufficient.'
        tool = run.get('tool', {})
        meaning = (metric['unit'], metric['method'], metric['scope'], metric.get('event', ''),
                   metric.get('source_counter', ''), metric.get('operation_count'),
                   metric.get('counter_contract', ''), metric.get('tool', tool.get('id')),
                   metric.get('tool_version', tool.get('version')),metric.get('work_definition_id',''),
                   metric.get('work_unit',''),metric.get('capture_scope',''),metric.get('capture_window',''),
                   metric.get('event_descriptor_id',''),metric.get('event_descriptor_version',0),metric.get('event_semantics_key',''))
        meanings.add(meaning)
        values.append(value)
        refs.extend(_refs(run, [metric.get('evidence')]))
    if len(meanings) != 1:
        return 'INCONCLUSIVE', [], refs, None, 'Metric samples have mixed collection semantics.'
    return 'PASS', values, refs, next(iter(meanings)), 'Measured samples are available within the recorded scope.'


def _completion(runs):
    if all(run['status'] == 'completed' for run in runs):
        return _row('execution', 'execution', True, 'PASS', 'All referenced runs completed.')
    unfinished = [run for run in runs if run['status'] != 'completed']
    if all((run.get('error') or {}).get('category') in ('unavailable', 'permission_denied')
           for run in unfinished):
        return _row('execution', 'execution', True, 'UNSUPPORTED',
                    'Required execution capability was unavailable for the unfinished runs.')
    return _row('execution', 'execution', True, 'INCONCLUSIVE',
                'A referenced run did not complete; inspect its recorded error.')


def _correctness(runs, *, id='correctness', required=True, origin='platform', description=None):
    states = []
    for run in runs:
        correctness = run['correctness']
        validations = run.get('solver_samples', [])
        declared = 'sample_validation' in run['request']['correctness']
        failed_validation = declared and (correctness.get('sample_validation_status') == 'failed' or
                                           any(sample['status'] == 'FAIL' for sample in validations))
        incomplete_validation = declared and (correctness.get('sample_validation_status') != 'passed' or
                                               len(validations) != len(run['samples']) or
                                               any(sample['status'] != 'PASS' for sample in validations))
        if correctness['status'] == 'failed' or failed_validation:
            states.append('FAIL')
        elif correctness['status'] == 'passed' and not incomplete_validation:
            states.append('PASS')
        elif (run.get('error') or {}).get('category') in ('unavailable', 'permission_denied'):
            states.append('UNSUPPORTED')
        else:
            states.append('INCONCLUSIVE')
    refs = [ref for run in runs for ref in _refs(run, [run['correctness'].get('evidence')])]
    from .comparison import combine_gates
    status = combine_gates(states)
    reason = {'FAIL': 'A declared correctness check failed.',
              'PASS': 'Declared oracle checks passed within their stated coverage.',
              'UNSUPPORTED': 'A required correctness check could not execute in this environment.',
              'INCONCLUSIVE': 'Declared correctness evidence is missing or incomplete.'}[status]
    return _row(id, 'correctness', required, status, reason, refs, origin=origin, description=description)


def _primary(runs, metric_name, metric_scope, *, auxiliary=False):
    values = [_measurements(run, metric_name, metric_scope) for run in runs]
    refs = [ref for _, _, evidence, _, _ in values for ref in evidence]
    states = [item[0] for item in values]
    from .comparison import combine_gates
    status = combine_gates(states)
    reasons = [item[4] for item in values if item[0] != 'PASS']
    meanings = {item[3] for item in values if item[0] == 'PASS'}
    if status == 'PASS' and len(meanings) != 1:
        status, reasons = 'INCONCLUSIVE', ['Compared metric semantics differ across runs.']
    reason = '; '.join(dict.fromkeys(reasons)) if reasons else 'Selected metric is Measured with compatible semantics.'
    return _row('auxiliary_metric' if auxiliary else 'primary_metric', 'measurement', True, status, reason, refs)


def _state(run):
    configured = run['request'].get('execution_state')
    if configured is None:
        return 'INCONCLUSIVE', [], 'No execution-state contract was declared.'
    entries = run.get('execution_state_evidence', [])
    refs = [ref for entry in entries for ref in _refs(run, entry.get('evidence_refs', []))]
    if any(entry['status'] == 'FAIL' or (entry.get('verification') or {}).get('status') == 'FAIL' for entry in entries):
        return 'FAIL', refs, 'A required state action or consumer state verification failed.'
    if 'verify' not in configured:
        return 'INCONCLUSIVE', refs, 'No consumer state verifier was declared.'
    if not entries:
        if (run.get('error') or {}).get('category') in ('unavailable', 'permission_denied'):
            return 'UNSUPPORTED', refs, 'Execution-state collection was unavailable.'
        return 'INCONCLUSIVE', refs, 'State actions and verification evidence were not recorded.'
    from .adapters import REGISTRY
    selected = run.get('tool', {}).get('id', run['request']['measurement']['tool'])
    if selected not in REGISTRY:
        return 'INCONCLUSIVE', refs, 'Actual adapter is not known.'
    batch = REGISTRY[selected].batch
    if batch and configured['scope'] == 'measurement_sample':
        return 'UNSUPPORTED', refs, 'Batch adapter cannot verify state before each internal sample.'
    if not run['samples']:
        return 'INCONCLUSIVE', refs, 'No formal sample is available to associate with state evidence.'
    expected = {0} if batch else {sample.get('index', index) for index, sample in enumerate(run['samples'])}
    groups = [entry.get('measurement_group') for entry in entries]
    if (len(groups) != len(expected) or set(groups) != expected or
            any(entry['scope'] != configured['scope'] for entry in entries)):
        return 'INCONCLUSIVE', refs, 'State evidence does not cover every requested measurement boundary.'
    if any(entry['status'] != 'PASS' or not entry.get('verification') or
           entry['verification']['status'] != 'PASS' or
           not any(action['name'] == 'verify' and action['status'] == 'PASS' for action in entry['actions'])
           for entry in entries):
        return 'INCONCLUSIVE', refs, 'A successful action alone does not establish state; consumer verification is required.'
    scope = 'whole batch invocation, not each internal sample' if batch else 'each observed measurement invocation'
    return 'PASS', refs, f'Consumer verifier passed for {scope}; only its declared coverage is checked.'


def _state_row(runs, *, id='execution_state', required=True, origin='platform', description=None):
    values = [_state(run) for run in runs]
    refs = [ref for _, evidence, _ in values for ref in evidence]
    from .comparison import combine_gates
    status = combine_gates([item[0] for item in values])
    reasons = list(dict.fromkeys(item[2] for item in values if item[0] != 'PASS'))
    states = [run['request'].get('execution_state') for run in runs]
    if status == 'PASS' and any(state != states[0] for state in states[1:]):
        status, reasons = 'FAIL', ['Declared execution-state contracts differ between runs.']
    reason = '; '.join(reasons) if reasons else values[0][2]
    return _row(id, 'execution_state', required, status, reason, refs, origin=origin, description=description)


def _unit_work_windows_match(runs, claim, condition):
    """Require same-sample, compatible recorded scopes before unit attribution."""
    name, scope = condition['metric'], condition.get('metric_scope')
    for run in runs:
        for sample in run['samples']:
            primary_metrics = derive_all_sample(sample) if claim['metric'] in ALL_DERIVATIONS else sample['metrics']
            work_metrics = derive_all_sample(sample) if name in ALL_DERIVATIONS else sample['metrics']
            primary = [m for m in primary_metrics if m['name'] == claim['metric'] and
                       (claim.get('metric_scope') is None or m.get('scope') == claim['metric_scope'])]
            work = [m for m in work_metrics if m['name'] == name and
                    (scope is None or m.get('scope') == scope)]
            if len(primary) != 1 or len(work) != 1:
                return False
            if name == 'work.items':
                if (work[0]['method']!='target-reported-invocation-work'
                        or work[0].get('capture_scope')!='target_process_and_inherited_threads'
                        or work[0].get('capture_window')!='whole-target-invocation'
                        or any(primary[0].get(k)!=work[0].get(k) for k in ('capture_scope','capture_window'))
                        or primary[0].get('evidence','').rpartition('/')[0] != work[0].get('evidence','').rpartition('/')[0]):
                    return False
                continue
            actual_scope = work[0]['scope']
            if name == 'phase.work':
                if (claim['metric'] != 'phase.inclusive_time' or '; counter=' not in actual_scope
                        or work[0].get('evidence') != primary[0].get('evidence')):
                    return False
                actual_scope = actual_scope.rsplit('; counter=', 1)[0]
            if actual_scope != primary[0]['scope']:
                return False
    return True


def _work(runs, condition, compare, claim):
    name, scope = condition['metric'], condition.get('metric_scope')
    values = [_measurements(run, name, scope) for run in runs]
    refs = [ref for _, _, evidence, _, _ in values for ref in evidence]
    from .comparison import combine_gates
    status = combine_gates([item[0] for item in values])
    reasons = list(dict.fromkeys(item[4] for item in values if item[0] != 'PASS'))
    if status == 'PASS':
        meanings = {item[3] for item in values}
        if len(meanings) != 1:
            status, reasons = 'INCONCLUSIVE', ['Work metric meanings differ across samples or runs.']
        elif name!='work.items' and values[0][3][0] not in ('count', 'bytes', 'byte', 'FLOP'):
            status, reasons = 'INCONCLUSIVE', ['The declared work unit is not a currently verifiable work count.']
        elif any(number < 0 for item in values for number in item[1]):
            status, reasons = 'FAIL', ['Recorded work counts must be nonnegative.']
        elif claim['kind'] == 'unit_efficiency' and any(number == 0 for item in values for number in item[1]):
            status, reasons = 'FAIL', ['A positive work count is required for a unit-efficiency claim.']
        elif claim['kind'] == 'unit_efficiency' and not _unit_work_windows_match(runs, claim, condition):
            status, reasons = 'INCONCLUSIVE', ['Work and primary metric do not share a verified sample scope.']
        elif not compare:
            status, reasons = 'INCONCLUSIVE', ['Work was measured in one run; equality requires a comparison.']
        else:
            observed = [number for item in values for number in item[1]]
            if len(set(observed)) == 1:
                reasons = ['Recorded work counts are equal in all compared samples; unrecorded work remains unknown.']
            elif all(len(set(item[1])) == 1 for item in values):
                status, reasons = 'FAIL', ['Recorded constant work counts differ between compared runs.']
            else:
                status, reasons = 'INCONCLUSIVE', ['Work varies within repetitions; unpaired counts do not establish equality.']
    return _row(condition['id'], condition['source'], condition['required'], status, '; '.join(reasons), refs,
                origin='declared', description=condition['description'])


def _condition(runs, condition, compare, claim):
    source = condition['source']
    args = dict(id=condition['id'], required=condition['required'], origin='declared', description=condition['description'])
    if source == 'declaration':
        return _row(source=source, status='INCONCLUSIVE', reason='Consumer declaration is not independent evidence.', **args)
    if source == 'correctness':
        return _correctness(runs, **args)
    if source == 'execution_state':
        return _state_row(runs, **args)
    return _work(runs, condition, compare, claim)


def _project(runs, *, comparison_metric=None, comparison_scope=None, compare=False):
    claims = [run['request'].get('claim') for run in runs]
    claim = next((item for item in claims if item is not None), None)
    if claim is None:
        states = [run['request'].get('execution_state') for run in runs]
        if all(state is None for state in states):
            return dict(schema_version=1, status='NOT_CONFIGURED', assessment_scope='none',
                        claim_kind=None, claim=None, claim_applicable=None,
                        conditions=[], limitations=list(LIMITATIONS))
        required = any(state is not None and state['required'] for state in states)
        rows = [_completion(runs), _correctness(runs), _state_row(runs, required=required)]
        return dict(schema_version=1, status=_aggregate(rows), assessment_scope='execution_state',
                    claim_kind=None, claim=None, claim_applicable=None, conditions=rows, limitations=list(LIMITATIONS)+[
                        'No performance claim was declared; state checks do not establish a specific claim.'])
    rows = []
    if any(item is None for item in claims):
        rows.append(_row('claim_contract', 'claim', True, 'INCONCLUSIVE', 'A referenced run has no claim declaration.'))
    elif any(item != claim for item in claims):
        rows.append(_row('claim_contract', 'claim', True, 'FAIL', 'Claim declarations differ between referenced runs.'))
    else:
        rows.append(_row('claim_contract', 'claim', True, 'PASS', 'All referenced runs have the same declared claim.'))
    applicable = None if not compare else (claim['metric'] == comparison_metric and
                                            claim.get('metric_scope') == comparison_scope)
    metric_name = comparison_metric if compare else claim['metric']
    metric_scope = comparison_scope if compare else claim.get('metric_scope')
    rows.extend((_completion(runs), _correctness(runs),
                 _primary(runs, metric_name, metric_scope, auxiliary=applicable is False)))
    if applicable is False:
        rows.append(_row('comparison_metric', 'claim', False, 'INCONCLUSIVE',
                         'Comparison metric or scope does not match the declared primary claim.'))
    states = [run['request'].get('execution_state') for run in runs]
    if any(state is not None for state in states):
        rows.append(_state_row(runs, required=any(state is not None and state['required'] for state in states)))
    rows.extend(_condition(runs, condition, compare, claim) for condition in claim['conditions'])
    limitations = list(LIMITATIONS)
    if claim['kind'] == 'unit_efficiency':
        limitations.append('Equal observed work counts do not prove equal unobserved work or establish a causal mechanism.')
    if claim['kind'] == 'mechanism_hypothesis':
        limitations.append('The mechanism remains a hypothesis; passing conditions is not causal proof.')
    return dict(schema_version=1, status=_aggregate(rows), assessment_scope='claim',
                claim_applicable=applicable, claim_kind=claim['kind'],
                claim={key: claim[key] for key in ('hypothesis', 'kind', 'applies_to', 'allowed_changes', 'metric', 'metric_scope') if key in claim},
                conditions=rows, limitations=limitations)


def evaluate_run_validity(result):
    """Evaluate one run without inferring between-run equality or benefit."""
    return _project([result])


def evaluate_comparison_validity(a, b, metric_name, metric_scope=None):
    """Evaluate a claim against both existing source runs, without recomputing statistics."""
    return _project([a, b], comparison_metric=metric_name, comparison_scope=metric_scope, compare=True)


def evaluate_paired_validity(blocks, metric_name, metric_scope=None):
    """Evaluate every paired member so a later failing block cannot be hidden."""
    runs = [member['run'] for block in blocks for member in block['members']]
    return _project(runs, comparison_metric=metric_name, comparison_scope=metric_scope, compare=True)


def apply_validity(result, validity):
    """Attach a separate claim gate while retaining existing statistics and decisions."""
    result['validity'] = validity
    if validity['status'] == 'NOT_CONFIGURED':
        return result
    from .comparison import combine_gates
    status = combine_gates([validity['status'], result['acceptance']['status']])
    reason = 'Existing acceptance and declared required-condition gates are both satisfied.' if status == 'PASS' else \
             'The existing acceptance or a required claim condition did not pass; inspect both results.'
    if validity['status'] not in ('FAIL', 'UNSUPPORTED') and validity['claim_applicable'] is False:
        status, reason = 'INCONCLUSIVE', 'This comparison does not evaluate the declared primary claim.'
    if validity['status'] not in ('FAIL', 'UNSUPPORTED') and validity['claim_kind'] is None:
        status, reason = 'INCONCLUSIVE', 'No performance claim was declared; state checks do not prove a claim.'
    if validity['claim_kind'] == 'mechanism_hypothesis' and status == 'PASS':
        status, reason = 'INCONCLUSIVE', 'A mechanism remains a hypothesis; the claim gate cannot prove causality.'
    result['claim_acceptance'] = {'status': status, 'reason': reason}
    return result
