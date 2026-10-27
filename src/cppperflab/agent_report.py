"""A compact, evidence-backed projection of an existing shared analysis view.

No metric, statistical result, or root-cause inference is calculated here.
"""
import json

from .errors import LabError
from .performance_summary import dimension
from .views import recorded_validity


def _pick(source, fields):
    return {key: source[key] for key in fields if key in source}


def _error(error):
    return None if error is None else _pick(error, ('category', 'message', 'exit_code'))


def _collection(items, total):
    return {'items': items, 'total': total, 'returned': len(items), 'omitted': total-len(items)}


def _subject(view, limit):
    overview, conditions = view['overview'], view['conditions']
    result = _pick(overview, ('name', 'analysis', 'metric', 'axis', 'variants', 'collection_id',
                              'baseline_id', 'candidate_id'))
    if 'workload' in overview:
        result['workload'] = _pick(overview['workload'], ('id', 'parameters'))
    if overview.get('correctness') is not None:
        result['correctness'] = _pick(overview['correctness'], ('status', 'description', 'meaning', 'evidence'))
    if overview.get('tool') is not None:
        result['tool'] = _pick(overview['tool'], ('id', 'tool', 'version', 'mode'))
    if 'measurement' in conditions:
        result['measurement'] = _pick(conditions['measurement'], ('tool', 'repetitions', 'warmups', 'cpu_affinity'))
    if 'target' in conditions:
        result['build'] = _pick(conditions['target']['build'], ('revision', 'compiler', 'flags', 'mode'))
    if 'execution_state' in conditions:
        result['execution_state'] = _pick(conditions['execution_state'],
            ('schema_version','scope','process','object_state','cache','external_state','required'))
    if 'execution_context' in conditions:
        context = conditions['execution_context']
        result['execution_context'] = _pick(context, ('kind', 'scope', 'concurrent_runs', 'resource_conditions',
                                                       'status', 'overlap_fraction', 'overlap'))
        if context.get('kind') == 'concurrent_ab':
            result['execution_context_note'] = 'Concurrent competing-pair conditions do not establish isolated serial performance.'
    if 'resource_conditions' in overview:
        result['resource_conditions'] = overview['resource_conditions']
    elif 'spec' in conditions and 'resource_conditions' in conditions['spec']:
        result['resource_conditions'] = conditions['spec']['resource_conditions']
    if view['view_kind'] != 'run':
        groups = {}
        for source in view.get('source_records', []):
            condition = _pick(source, ('workload', 'tool', 'measurement', 'build', 'execution_context'))
            if condition:
                key = json.dumps(condition, sort_keys=True, allow_nan=False)
                if key not in groups:
                    groups[key] = {'example_run_id': source['run_id'], 'run_count': 0, **condition}
                groups[key]['run_count'] += 1
        values = list(groups.values())
        result['source_conditions'] = _collection(values[:limit], len(values))
        result['source_conditions_note'] = ('Grouped by displayed workload, tool, measurement and build fields only; '
            'this does not establish matching executables, inputs or complete runtime conditions.')
    return result


def _assessment(view):
    assessment = view['summary'].get('assessment')
    if assessment is None:
        return None
    result = _pick(assessment, ('schema_version', 'correctness', 'acceptance', 'end_to_end_improvement', 'optimization_accepted', 'limitations'))
    result['cases'] = [_pick(case, ('case_id', 'correctness', 'acceptance', 'end_to_end_improvement', 'work_attribution'))
                       for case in assessment['cases']]
    return result


def _decision_subset(decision):
    # Preserve each existing gate separately. Do not derive a combined decision.
    return {k: decision[k] for k in ('correctness', 'comparability', 'statistics', 'non_regression',
                                    'performance_target', 'improvement', 'validity', 'claim_support',
                                    'optimization_accepted') if k in decision}


def _critical(comparison):
    if not comparison['comparable'] or comparison.get('classification') == 'observed_regression':
        return True
    if comparison.get('acceptance', {}).get('status') != 'PASS':
        return True
    if recorded_validity(comparison)['status'] not in ('PASS','NOT_CONFIGURED'):
        return True
    if 'claim_acceptance' in comparison and comparison['claim_acceptance']['status'] != 'PASS':
        return True
    decisions = comparison.get('decisions', {})
    if decisions.get('improvement', {}).get('status') == 'SUPPORTED_REGRESSION':
        return True
    return any(isinstance(gate, dict) and gate.get('status') in ('FAIL', 'UNSUPPORTED')
               for gate in decisions.values())


def _comparison(row):
    fields = ('comparison_id', 'study_id', 'source_study_id', 'baseline_id', 'candidate_id',
              'case_id', 'profile_id', 'assessment_role', 'assessment_gate', 'metric', 'metric_scope',
              'unit', 'comparable', 'classification', 'baseline_median', 'candidate_median',
              'speedup', 'change_fraction', 'runtime_reduction', 'bootstrap_95_percent_speedup',
              'coefficient_of_variation', 'paired_ratio_cv', 'sample_unit', 'sufficient_samples',
              'stable_samples', 'evidence_state', 'input_evidence_state', 'ratio_semantics',
              'acceptance', 'normalized_cost', 'claim_acceptance')
    result = _pick(row, fields)
    result['validity'] = recorded_validity(row)
    if 'decisions' in row:
        result['decisions'] = _decision_subset(row['decisions'])
    if 'baseline_samples' in row:
        result['sample_counts'] = {'baseline': len(row['baseline_samples']), 'candidate': len(row['candidate_samples'])}
    if 'execution_context' in row:
        result['execution_context'] = _pick(row['execution_context'], ('kind', 'scope', 'concurrent_runs', 'resource_conditions'))
    if row.get('error') is not None:
        result['error'] = _error(row['error'])
    return result


def _metric(group):
    summary = group['summary']
    row = _pick(summary, ('name', 'unit', 'method', 'scope', 'n', 'median', 'mean', 'cv', 'evidence_state'))
    if 'evidence_state' not in row:
        states = {sample['evidence_state'] for sample in group['samples'] if 'evidence_state' in sample}
        row['evidence_state'] = next(iter(states)) if len(states) == 1 else None
    row.update(_pick(group, ('run_id', 'case_id', 'variant', 'profile_id')))
    ids = list(dict.fromkeys(sample['evidence_id'] for sample in group['samples'] if sample.get('evidence_id')))
    row['evidence_ids'] = ids[:3]
    row['evidence_total'] = len(ids)
    return row


def _metrics(view):
    # Study views may not populate metric groups; the shared performance summary
    # already holds per-run observations without combining their scopes.
    groups = list(view['metrics'])
    if not groups:
        for category in view['summary'].get('dimensions', []):
            for item in category['observations']:
                if 'metric' in item:
                    groups.append({'summary': item['metric'], 'run_id': item['run_id'],
                                   'samples': [{'evidence_id': item_id} for item_id in item.get('evidence_ids', [])]})
    order = ('timing', 'resources', 'work', 'execution', 'instruction_quality', 'correctness', 'unclassified')
    buckets = {key: [] for key in order}
    for group in groups:
        buckets[dimension(group['summary']['name'])].append(group)
    # Retain the existing order within each dimension, while giving outcome,
    # resource, work and diagnostic observations a chance to be represented.
    result = []
    for index in range(max((len(bucket) for bucket in buckets.values()), default=0)):
        for key in order:
            if index < len(buckets[key]):
                result.append(buckets[key][index])
    return result


def _attention(view):
    groups = {}
    for item in view['quality']:
        key = tuple(item.get(k) for k in ('code', 'severity', 'metric', 'evidence_state', 'message'))
        if key not in groups:
            groups[key] = _pick(item, ('code', 'severity', 'metric', 'evidence_state', 'message'))
            groups[key]['count'] = 0
        groups[key]['count'] += 1
    issues = list(groups.values())
    missing = {}
    for item in view['observations']:
        state = item.get('evidence_state')
        if state in ('Missing', 'Invalid', 'Unsupported'):
            key = (item.get('name'), state, item.get('reason'))
            if key not in missing:
                missing[key] = {'metric': key[0], 'evidence_state': state, 'reason': key[2], 'count': 0}
            missing[key]['count'] += 1
    sources = []
    for source in view.get('source_records', []):
        if source['status'] != 'completed' or source.get('correctness_status') == 'failed':
            sources.append(_pick(source, ('run_id', 'status', 'error', 'correctness_status')))
    lifecycle = view.get('lifecycle') or {}
    for run in lifecycle.get('runs', []):
        candidate = run.get('candidate') or {}
        if run.get('run_status') not in ('completed', 'running') or candidate.get('candidate_state') == 'withdrawn' or candidate.get('superseded_by'):
            current = next((item for item in sources if item['run_id'] == run['run_id']), None)
            if current is None:
                current = {'run_id': run['run_id'], 'status': run.get('run_status'),
                           'error': _error(run.get('source_error'))}
                sources.append(current)
            current.update(candidate_state=candidate.get('candidate_state'),
                           superseded_by=candidate.get('superseded_by'))
    for candidate in [view.get('candidate'), *view['conditions'].get('source_candidates', [])]:
        if candidate and (candidate.get('candidate_state') == 'withdrawn' or candidate.get('superseded_by')):
            row = _pick(candidate, ('run_id', 'candidate_state', 'superseded_by', 'revision'))
            existing = next((item for item in sources if item.get('run_id') == row.get('run_id')), None)
            if existing is None:
                sources.append(row)
            else:
                existing.update(row)
    validity_issues=[]
    for source in view.get('source_records', []):
        validity=source.get('validity')
        if validity is not None and (validity['status'] not in ('PASS','NOT_CONFIGURED') or validity['conditions']):
            validity_issues.append({'run_id':source['run_id'],'validity':validity})
    return {'quality': issues, 'unavailable_observations': list(missing.values()),
            'sources_requiring_review': sources, 'source_validity_issues':validity_issues}


def _workflow(view):
    workflow=view.get('workflow')
    if workflow is None:
        return None
    result=_pick(workflow,('schema_version','study_id','phase','source_study_ids','selection_reason',
        'selected_run_id','freeze_path','freeze_digest','frozen_at','freeze_monotonic_ns',
        'run_started_monotonic_ns','clock','status','calibration','calibrations','source_confirmations','limitations','reason',
        'optimization_accepted','parent_study_status','source_context'))
    result['scope_note']=('Phase describes the requested role. Check the parent Study and workflow status; '
                          'provenance does not establish statistical independence, performance improvement or adoption.')
    return result


def _state_evidence(view, limit):
    def action_summary(action):
        row=_pick(action,('name','status','evidence_refs'))
        if 'error' in action:row['error']=_error(action['error'])
        return row
    def compact(entry):
        row=_pick(entry,('schema_version','measurement_group','scope','status','evidence_refs','limitations'))
        if 'verification' in entry:
            row['verification']=None if entry['verification'] is None else _pick(entry['verification'],('status','description'))
        if 'actions' in entry:
            row['actions']=[action_summary(action) for action in entry['actions']]
        if 'cleanup' in entry:
            cleanup=entry['cleanup']
            row['cleanup']=_pick(cleanup,('schema_version','status','evidence_refs'))
            if 'action' in cleanup:row['cleanup']['action']=action_summary(cleanup['action'])
        return row
    entries=view.get('execution_state_evidence',[])
    critical=[entry for entry in entries if entry['status']!='PASS' or
              (entry.get('cleanup') is not None and entry['cleanup']['status']!='PASS')]
    ordinary=[entry for entry in entries if entry not in critical]
    return _collection([compact(entry) for entry in critical+ordinary[:limit]],len(entries))


def _follow_up(view, store_root, subject):
    prefix = ['perflab', 'analyze', view['record_id'], '--kind', view['view_kind']]
    def section(name):
        return {'section': name, 'argv': prefix + ['--section', name, '--store', str(store_root), '--json']}
    names = ['conditions', 'comparisons', 'metrics', 'quality', 'evidence', 'validity']
    if view.get('workflow') is not None:names.append('workflow')
    if view.get('execution_state_evidence'):names.append('execution_state_evidence')
    if any(row['evidence_state'] in ('Missing','Invalid','Unsupported') for row in view['observations']):
        names.append('observations')
    if view.get('lifecycle') is not None:
        names.append('lifecycle')
    if view.get('candidate') is not None:
        names.append('candidate')
    if view['phases']:
        names.append('phases')
    if view['phase_summaries']:
        names.append('phase_summaries')
    if view['growth']:
        names.append('growth')
    if view['hypotheses']:
        names.append('hypotheses')
    result = [section(name) for name in names]
    if subject.get('source_conditions', {}).get('omitted'):
        result.append(section('all'))
    for condition in subject.get('source_conditions', {}).get('items', []):
        result.append({'section': 'conditions', 'argv': ['perflab', 'analyze', condition['example_run_id'],
            '--kind', 'run', '--section', 'conditions', '--store', str(store_root), '--json']})
    return result


def build_agent_report(view, store_root, limit=5):
    """Project one view into a small report, retaining every exceptional comparison."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise LabError('invalid_request', 'Agent report limit must be an integer from 1 to 200')
    comparisons = []
    findings = view['summary'].get('findings', [])
    for index, original in enumerate(view['comparisons']):
        row = dict(original)
        # The shared summary has already projected existing Comparison decisions.
        # Older records may omit those persisted fields; reuse the matching finding.
        if index < len(findings):
            finding = findings[index]
            if all(finding.get(key) == original.get(key) for key in ('metric', 'baseline_id', 'candidate_id')):
                for key in ('decisions', 'normalized_cost'):
                    if key not in row and key in finding:
                        row[key] = finding[key]
        comparisons.append(row)
    critical = [c for c in comparisons if _critical(c)]
    ordinary = [c for c in comparisons if not _critical(c)]
    selected = critical + ordinary[:limit]
    groups = _metrics(view)
    hotspots = [_pick(item, ('run_id', 'case_id', 'variant', 'sample', 'symbol', 'dso',
                              'self_percent', 'inclusive_percent', 'evidence_id')) for item in view['hotspots'][:limit]]
    hypotheses = [_pick(item, ('run_id', 'case_id', 'kind', 'statement', 'evidence', 'next_measurement'))
                  for item in view['hypotheses'][:limit]]
    coverage = [{'dimension': d['id'], 'coverage': d['coverage'], 'missing_metrics': d['missing_metrics']}
                for d in view['summary'].get('dimensions', [])]
    subject = _subject(view, limit)
    return {'schema_version': 1, 'report_type': 'agent', 'record_id': view['record_id'],
            'view_kind': view['view_kind'], 'status': view['status'], 'error': _error(view['error']),
            'optimization_accepted': False, 'subject': subject, 'assessment': _assessment(view),
            'validity':view['validity'], 'workflow':_workflow(view), 'execution_state_evidence':_state_evidence(view,limit),
            'comparisons': _collection([_comparison(row) for row in selected], len(comparisons)),
            'metrics': _collection([_metric(group) for group in groups[:limit]], len(groups)),
            'hotspots': _collection(hotspots, len(view['hotspots'])),
            'hypotheses': _collection(hypotheses, len(view['hypotheses'])),
            'attention': _attention(view), 'coverage': coverage, 'limitations': view['limitations'],
            'follow_up': _follow_up(view, store_root, subject)}
