"""Freeze and verify declared Study phases using existing Run/Study evidence.

This module neither executes targets nor computes comparison statistics.  It keeps
consumer selection and calibration evidence separate from performance acceptance.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import time

from .environment import identity_snapshot
from .errors import LabError
from .storage import utc, write_json

_context = ContextVar('cppperflab_decision_workflow', default=None)


def current():
    """Return the parent Study context, or None for an ordinary Run."""
    return _context.get()


@contextmanager
def session(record):
    """Bind one Study's frozen context; an unconfigured child inherits its parent."""
    if record is None:
        yield
        return
    context = {key: record[key] for key in ('study_id', 'phase', 'freeze_digest', 'freeze_monotonic_ns', 'clock')}
    token = _context.set(context)
    try:
        yield
    finally:
        _context.reset(token)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _clock():
    try:
        return {'name': 'CLOCK_MONOTONIC',
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'time_namespace': os.readlink('/proc/self/ns/time')}
    except OSError as error:
        raise LabError('unavailable', 'Cannot identify the monotonic clock context for a workflow',
                       {'reason': str(error)}) from error


def _study_run_ids(store, study_id, seen=None):
    """Follow stored Study children, not its preceding phase references."""
    if seen is None:
        seen = set()
    if study_id in seen:
        return set()
    seen.add(study_id)
    study = store.read(study_id, 'study')
    result = {member['run_id'] for member in study['runs']}
    children = [job['study_id'] for job in study.get('jobs', []) if job.get('study_id') is not None]
    lifecycle = study.get('lifecycle')
    if lifecycle is not None:
        children.extend(child['record_id'] for child in lifecycle['children'] if child['record_kind'] == 'study')
    for child in children:
        result.update(_study_run_ids(store, child, seen))
    return result


def _candidate_keys(spec, requests):
    analysis = spec.get('analysis', 'growth')
    if analysis == 'paired':
        return ['candidate']
    if analysis == 'cases':
        variants = {row['candidate'] for row in spec['comparisons']}
        if len(variants) != 1:
            raise LabError('invalid_request', 'A confirmation case matrix must freeze exactly one candidate variant')
        return [key for key in requests if json.loads(key)[-1] in variants]
    if analysis == 'assessment':
        return [key for key in requests if json.loads(key)[-1] == 'candidate']
    variants = {variant['id'] for variant in spec['variants'][1:]}
    if len(variants) != 1:
        raise LabError('invalid_request', 'A confirmation scaling study must freeze exactly one candidate variant')
    return [key for key in requests if json.loads(key)[0] in variants]


def _program_identity(request, identity):
    paths = {request['target']['argv'][0], *request['target']['build']['artifacts']}
    if not paths <= identity['artifacts'].keys():
        raise LabError('storage_failed', 'Recorded candidate identity is missing declared program artifacts')
    return {path: identity['artifacts'][path] for path in sorted(paths)}


def _request_without_name(request):
    return {key: value for key, value in request.items() if key != 'name'}


def _confirmation_comparisons(study):
    """Copy existing source decisions without deriving an improvement verdict."""
    rows = []
    for index, comparison in enumerate(study['comparisons']):
        row = {key: comparison[key] for key in ('comparison_id', 'metric', 'metric_scope',
               'case_id', 'profile_id', 'assessment_role', 'classification') if key in comparison}
        row['index'] = index
        validity = comparison.get('validity')
        row['validity'] = None if validity is None else {'status': validity['status']}
        row['acceptance'] = comparison.get('acceptance')
        row['claim_acceptance'] = comparison.get('claim_acceptance')
        row['improvement'] = comparison.get('decisions', {}).get('improvement')
        rows.append(row)
    return rows


def _candidate_run_ids(comparison):
    return set(comparison['candidate_run_ids'] if 'candidate_run_ids' in comparison else [comparison['candidate_id']])


def _exploration_candidate_run_ids(study):
    spec = study['spec']
    analysis = spec.get('analysis', 'growth')
    if analysis in ('paired', 'assessment'):
        variants = {'candidate'}
    elif analysis == 'cases':
        variants = {row['candidate'] for row in spec['comparisons']}
    else:
        variants = {row['id'] for row in spec['variants'][1:]}
    return {row['run_id'] for row in study['runs'] if row['variant'] in variants}


def _freeze_file(record, store):
    if record.get('freeze_path') != 'workflow-freeze.json':
        raise LabError('storage_failed', 'Invalid frozen workflow evidence path')
    directory = store.record_path(record['study_id'], 'study').parent
    path = directory / record['freeze_path']
    try:
        frozen = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise LabError('storage_failed', 'Cannot read the frozen workflow record', {'reason': str(error)}) from error
    if not isinstance(frozen, dict) or 'freeze_digest' not in frozen:
        raise LabError('storage_failed', 'Malformed frozen workflow record')
    digest = frozen.pop('freeze_digest')
    if digest != record['freeze_digest'] or _digest(frozen) != digest:
        raise LabError('storage_failed', 'Frozen workflow record differs from its digest')
    return frozen


def _matching_requests(frozen, request, identity):
    return [key for key, saved in frozen['requests'].items()
            if _request_without_name(saved) == _request_without_name(request) and
            frozen['identities'][key] == identity]


def verify_frozen_request(request, identity, store):
    """Reject changed requests or identities before the target is executed."""
    context = current()
    if context is None:
        return
    frozen = _freeze_file({**context, 'freeze_path': 'workflow-freeze.json'}, store)
    if not _matching_requests(frozen, request, identity):
        raise LabError('incomparable', 'Request or artifact identity differs from the frozen configurations')


def _calibration_compatible(source, frozen, spec, requests, identities):
    """Check static conditions; actual runtime environment is checked at finish."""
    if source['status'] != 'completed' or source['workflow']['status'] != 'PASS':
        return []
    calibration = source['workflow'].get('calibration')
    if calibration is None or calibration['status'] != 'PASS':
        return []
    if spec.get('analysis') not in ('paired', 'assessment'):
        return []
    source_spec = frozen['spec']
    if spec.get('analysis') == 'paired':
        primary = {'metric': spec['metric'], 'metric_scope': spec.get('metric_scope')}
    else:
        primary_rows = [item for profile in spec['profiles'] for item in profile['comparisons']
                        if item['role'] == 'end_to_end']
        primary = primary_rows[0]
    if source_spec['metric'] != primary['metric'] or source_spec.get('metric_scope') != primary.get('metric_scope'):
        return []
    if source_spec['resource_conditions'] != spec['resource_conditions']:
        return []
    left = frozen['requests']['baseline']
    left_identity = frozen['identities']['baseline']
    matches = []
    for key, request in requests.items():
        # Repetition count and timeout describe experiment budget, not target state.
        def controls(item):
            measurement = {k: v for k, v in item['measurement'].items() if k not in ('repetitions', 'timeout_seconds')}
            return {'workload': item['workload'], 'target': item['target'], 'measurement': measurement,
                    'correctness': item['correctness'], 'execution_state': item.get('execution_state')}
        if controls(left) == controls(request) and left_identity == identities[key]:
            matches.append(key)
    return matches


def begin(spec, requests, store, study_id, directory):
    """Freeze a configured Study before any child Run is launched."""
    if 'workflow' not in spec:
        return None
    workflow = spec['workflow']
    phase = workflow['phase']
    # A/A and repeated configurations can be byte-identical apart from their
    # presentation name. Snapshot them once while freezing; every actual Run
    # still checks its own identity against the frozen value.
    snapshots = {}
    identities = {}
    for key, request in requests.items():
        signature = _canonical(_request_without_name(request))
        if signature not in snapshots:
            snapshots[signature] = identity_snapshot(request)
        identities[key] = snapshots[signature]
    if phase == 'calibration':
        if spec.get('analysis') != 'paired':
            raise LabError('invalid_request', 'Calibration requires the existing paired Study design')
        if _request_without_name(requests['baseline']) != _request_without_name(requests['candidate']) or identities['baseline'] != identities['candidate']:
            raise LabError('incomparable', 'A/A calibration requires identical target, input, state and measurement configuration')
        if 'min_speedup' in spec['policy']:
            raise LabError('invalid_request', 'A/A calibration cannot declare a minimum speedup target')

    sources = []
    required_phase = {'confirmation': 'exploration', 'acceptance': 'confirmation'}.get(phase)
    selected_id = workflow.get('selected_run_id')
    selected = None
    if required_phase is not None:
        selected = store.read(selected_id)
        if selected['status'] != 'completed' or selected['correctness']['status'] != 'passed':
            raise LabError('invalid_request', 'Selected source Run must be completed and pass its declared oracle')
    selected_belongs = False
    for source_id in workflow['source_study_ids']:
        study = store.read(source_id, 'study')
        previous = study.get('workflow')
        if previous is None:
            raise LabError('invalid_request', 'Source Study has no explicitly recorded workflow phase', {'study_id': source_id})
        source_runs = sorted(_study_run_ids(store, source_id))
        belongs = selected_id in source_runs if selected_id is not None else False
        is_confirmed_candidate = ((phase != 'confirmation' or selected_id in _exploration_candidate_run_ids(study)) and
                                  (phase != 'acceptance' or
                                   any(selected_id in _candidate_run_ids(row) for row in study['comparisons'])))
        if previous['phase'] == required_phase and study['status'] == 'completed' and previous['status'] == 'PASS' and belongs and is_confirmed_candidate:
            # The source phase must itself be backed by its original frozen Run.
            _verify_run(selected, _freeze_file(previous, store), previous, store)
            selected_belongs = True
        sources.append({'study_id': source_id, 'phase': previous['phase'], 'status': study['status'],
                        'workflow_status': previous['status'], 'run_ids': source_runs})
        if phase == 'acceptance' and previous['phase'] == 'confirmation':
            sources[-1]['confirmation_comparisons'] = _confirmation_comparisons(study)
    if required_phase is not None:
        if not selected_belongs:
            raise LabError('invalid_request', 'Selected Run is not in a completed source Study of the required phase',
                           {'selected_run_id': selected_id, 'required_phase': required_phase})
        candidates = _candidate_keys(spec, requests)
        if not candidates:
            raise LabError('invalid_request', 'A confirmation or acceptance needs an identifiable candidate')
        expected = _program_identity(selected['request'], selected['identity'])
        for key in candidates:
            if _program_identity(requests[key], identities[key]) != expected:
                raise LabError('incomparable', 'Candidate program/artifact identity differs from the selected Run', {'candidate_key': key})
        if spec.get('analysis') == 'paired' and requests['candidate']['target']['argv'] != selected['request']['target']['argv']:
            raise LabError('incomparable', 'Paired candidate argv differs from the selected Run')

    selection = None if selected is None else {
        'run_id': selected_id,
        'program_artifacts': expected,
        'target_argv': selected['request']['target']['argv'] if spec.get('analysis') == 'paired' else None,
        'scope': 'Selected program and declared build artifacts; current configurations are frozen separately.'}

    clock = _clock()
    frozen = {'schema_version': 1, 'study_id': study_id, 'phase': phase, 'frozen_at': utc(),
              'freeze_monotonic_ns': time.monotonic_ns(), 'clock': clock,
              'spec': spec, 'requests': requests, 'identities': identities,
              'sources': sources, 'selected_run_id': selected_id, 'selection': selection}
    digest = _digest(frozen)
    record = {'schema_version': 1, 'study_id': study_id, 'phase': phase,
              'source_study_ids': workflow['source_study_ids'], 'selection_reason': workflow['selection_reason'],
              'selected_run_id': selected_id, 'freeze_path': 'workflow-freeze.json',
              'freeze_digest': digest, 'frozen_at': frozen['frozen_at'],
              'freeze_monotonic_ns': frozen['freeze_monotonic_ns'], 'clock': clock,
              'status': 'RUNNING', 'calibration': None, 'calibrations': [],
              'source_confirmations': [{'study_id': source['study_id'],
                  'comparisons': source['confirmation_comparisons']} for source in sources
                  if 'confirmation_comparisons' in source],
              'limitations': ['New Run IDs and frozen provenance do not establish statistical independence or eliminate drift.',
                              'Selected candidate identity covers executable and explicitly declared build artifacts; current configurations are separately frozen.',
                              'Cross-input equivalence, unrecorded attempts and generalization remain consumer responsibilities.',
                              'A/A diagnoses this declared control; it neither corrects A/B results nor proves absence of bias.'],
              'optimization_accepted': False}
    for source in sources:
        if source['phase'] != 'calibration':
            continue
        source_study = store.read(source['study_id'], 'study')
        source_record = source_study['workflow']
        source_freeze = _freeze_file(source_record, store)
        if source_study['status'] == 'completed' and source_record['status'] == 'PASS':
            if source_record['calibration'] != _calibration_result(source_freeze['spec'], source_study):
                raise LabError('storage_failed', 'Recorded A/A result differs from its original comparison')
        matches = _calibration_compatible(source_study, source_freeze, spec, requests, identities)
        record['calibrations'].append({'study_id': source['study_id'],
            'status': source_record.get('calibration', {}).get('status') if source_record.get('calibration') else 'INCONCLUSIVE',
            'source_ended_at': source_record.get('ended_at'),
            'matched_request_keys': matches, 'applicable': None if matches else False,
            'reason': 'Static controls match; measured runtime context still requires verification.' if matches else
                      'Calibration did not pass or declared target, input, resource conditions or metric differ.'})
    write_json(Path(directory) / 'workflow-freeze.json', {**frozen, 'freeze_digest': digest})
    return record


def _same_runtime(left, right):
    tool_keys = ('id', 'version', 'mode')
    env_keys = ('os', 'kernel', 'machine', 'hostname', 'cpu_model', 'cpu_flags', 'allowed_cpus', 'governor')
    return (all(left['tool'][key] == right['tool'][key] for key in tool_keys) and
            all(left['environment'][key] == right['environment'][key] for key in env_keys))


def _verify_run(run, frozen, record, store):
    context = run.get('workflow_context')
    required_context = {key: record[key] for key in ('study_id', 'phase', 'freeze_digest', 'freeze_monotonic_ns', 'clock')}
    if context is None or any(context.get(key) != value for key, value in required_context.items()):
        raise LabError('incomparable', 'Run lacks this Study\'s frozen workflow context', {'run_id': run['run_id']})
    if type(context.get('run_started_monotonic_ns')) is not int or context['run_started_monotonic_ns'] < record['freeze_monotonic_ns']:
        raise LabError('incomparable', 'Run was not started after the workflow freeze', {'run_id': run['run_id']})
    actual_clock = run['workflow_cost']['clock']
    if any(actual_clock.get(key) != record['clock'][key] for key in ('name', 'boot_id', 'time_namespace')):
        raise LabError('incomparable', 'Run and workflow use different monotonic clock contexts', {'run_id': run['run_id']})
    matches = _matching_requests(frozen, run['request'], run['identity'])
    if not matches:
        raise LabError('incomparable', 'Run request or recorded artifact identity differs from the frozen configurations', {'run_id': run['run_id']})
    from .adapters import REGISTRY
    batch = REGISTRY[run['tool']['id']].batch
    expected = 1 if batch else run['request']['measurement']['repetitions']
    directory = store.record_path(run['run_id']).parent
    executions = list(directory.glob('measurement-*/execution.json'))
    if len(executions) != expected:
        raise LabError('incomparable', 'Formal measurement execution evidence is incomplete', {'run_id': run['run_id'], 'expected': expected, 'observed': len(executions)})
    for path in executions:
        try:
            execution = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise LabError('storage_failed', 'Cannot read formal measurement execution evidence', {'run_id': run['run_id'], 'reason': str(error)}) from error
        if (execution['status'] != 'completed' or execution['returncode'] != 0 or
                type(execution.get('started_monotonic_ns')) is not int or
                execution['started_monotonic_ns'] < record['freeze_monotonic_ns']):
            raise LabError('incomparable', 'Formal measurement did not execute successfully after the freeze', {'run_id': run['run_id'], 'evidence': str(path.relative_to(directory))})
    return matches


def _verify_comparison_sources(comparisons, current_runs):
    for comparison in comparisons:
        references = (comparison.get('baseline_run_ids', [comparison['baseline_id']]) +
                      comparison.get('candidate_run_ids', [comparison['candidate_id']]))
        if not set(references) <= set(current_runs):
            raise LabError('incomparable', 'Comparison uses a Run outside this workflow')
        for block in comparison.get('block_pairs', []):
            for side in ('baseline', 'candidate'):
                for sample in block['members'][side]:
                    if sample['run_id'] not in current_runs or sample['sample_index'] not in current_runs[sample['run_id']]:
                        raise LabError('incomparable', 'Paired comparison references a sample outside this workflow')


def _calibration_result(spec, result):
    selected = [row for row in result['comparisons']
                if row['metric'] == spec['metric'] and row.get('metric_scope') == spec.get('metric_scope')]
    if len(selected) != 1:
        raise LabError('internal_error', 'Completed A/A Study has no unique primary comparison')
    comparison = selected[0]
    threshold = spec['policy']['max_regression_fraction']
    lower, upper = 1 / (1 + threshold), 1 + threshold
    low, high = comparison['bootstrap_95_percent_speedup']
    validity = comparison.get('validity', {'status': 'NOT_CONFIGURED'})['status']
    if not comparison['sufficient_samples'] or not comparison['stable_samples'] or validity not in ('PASS', 'NOT_CONFIGURED'):
        status = 'INCONCLUSIVE'
    elif high < lower or low > upper:
        status = 'FAIL'
    elif lower <= low and high <= upper:
        status = 'PASS'
    else:
        status = 'INCONCLUSIVE'
    return {'schema_version': 1, 'status': status, 'metric': spec['metric'],
            'metric_scope': spec.get('metric_scope'), 'ratio_interval_95_percent': [low, high],
            'declared_max_regression_fraction': threshold, 'acceptance_band': [lower, upper],
            'comparison_id': comparison.get('comparison_id'),
            'meaning': 'Descriptive paired interval relative to the preregistered A/A band; not a bias correction or proof of independence.'}


def _finalize(record, result, store):
    frozen = _freeze_file(record, store)
    if result['study_id'] != record['study_id'] or result['spec'] != frozen['spec']:
        raise LabError('incomparable', 'Study specification changed after workflow freeze')
    record['ended_at'] = utc()
    record['ended_monotonic_ns'] = time.monotonic_ns()
    if result['status'] != 'completed':
        record['status'] = 'INCONCLUSIVE'
        record['reason'] = 'Study did not complete; no successful-subset workflow verdict is assigned.'
        return record

    if record['phase'] in ('confirmation', 'acceptance') and not result['comparisons']:
        raise LabError('incomparable', 'A completed confirmation or acceptance requires an actual comparison')

    ids = [member['run_id'] for member in result['runs']]
    if not ids or len(ids) != len(set(ids)):
        raise LabError('incomparable', 'Completed workflow must contain distinct fresh Runs')
    source_ids = {run_id for source in frozen['sources'] for run_id in source['run_ids']}
    if source_ids.intersection(ids):
        raise LabError('incomparable', 'Workflow cannot reuse a source Study Run or its samples')
    runs = {}
    matching = {}
    for run_id in ids:
        run = store.read(run_id)
        if run['status'] != 'completed' or run['correctness']['status'] != 'passed' or not run['samples']:
            raise LabError('incomparable', 'Completed workflow contains a failed or unmeasured Run', {'run_id': run_id})
        matching[run_id] = _verify_run(run, frozen, record, store)
        runs[run_id] = {sample['index'] for sample in run['samples']}
    _verify_comparison_sources(result['comparisons'], runs)
    if record['phase'] == 'calibration':
        record['calibration'] = _calibration_result(frozen['spec'], result)
    for item in record['calibrations']:
        if item['applicable'] is False:
            continue
        source = store.read(item['study_id'], 'study')
        source_run = next(store.read(member['run_id']) for member in source['runs'] if member['variant'] == 'baseline')
        relevant = [store.read(run_id) for run_id, keys in matching.items()
                    if set(keys).intersection(item['matched_request_keys'])]
        item['applicable'] = bool(relevant) and all(_same_runtime(source_run, run) for run in relevant)
        item['reason'] = ('Declared controls and observed tool/environment match; background load, drift and independence remain unproved.' if item['applicable'] else
                          'Actual measured tool/environment does not match the calibration, or no matching Run was recorded.')
    record['status'] = 'PASS'
    record['reason'] = 'Frozen source, request and execution provenance checks passed; performance and adoption are separate decisions.'
    record['run_ids'] = ids
    return record


def finalize(record, result, store):
    """Publish phase provenance; preserve failed Study errors and expose invariant failures."""
    if record is None:
        return None
    try:
        return _finalize(record, result, store)
    except LabError as error:
        record['status'] = 'FAIL'
        record['reason'] = str(error)
        record['ended_at'] = utc()
        record['ended_monotonic_ns'] = time.monotonic_ns()
        raise


def finalize_into(result, store):
    """Finalize a Study without replacing an earlier execution failure."""
    record = result.get('workflow')
    if record is None:
        return
    try:
        finalize(record, result, store)
    except LabError as error:
        if result['status'] == 'completed':
            result['status'] = 'failed'
            result['error'] = error.as_dict()
        else:
            result['workflow_error'] = error.as_dict()
