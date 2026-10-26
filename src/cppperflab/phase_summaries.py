"""Normalize caller-reported phase summaries without inventing intervals."""
import json
import re
from decimal import Decimal
from pathlib import Path

from .errors import LabError
from .metrics import observation
from .protocol import fields, metric, positive, require

_STATES = ('Measured', 'Estimated')
_WORK_STATES = ('Measured', 'Estimated', 'Modeled')


def _number(value, name):
    positive(value, name, zero=True)


def _parse(raw, path):
    fields(raw, ('schema_version', 'representation', 'scope', 'stages', 'source'),
           ('schema_version', 'representation', 'scope', 'stages', 'source'), 'phase summary')
    require(type(raw['schema_version']) is int and raw['schema_version'] == 1,
            'Unsupported phase summary version')
    require(raw['representation'] == 'summary', 'Expected summary representation')
    require(isinstance(raw['scope'], str) and bool(raw['scope'].strip()), 'Phase summary scope required')
    require(isinstance(raw['stages'], list), 'stages must be an array')
    require(isinstance(raw['source'], dict), 'source provenance must be an object')
    source = raw['source']
    source_fields = ('input_sha256', 'mapping_sha256', 'input_lines', 'matched_lines',
                     'ignored_lines', 'source_path', 'mapping_path')
    require(set(source_fields) <= set(source), 'Missing phase summary source provenance fields')
    require(not set(source) - set(source_fields), 'Unknown phase summary source provenance fields')
    for key in ('input_sha256', 'mapping_sha256'):
        require(isinstance(source[key], str) and re.fullmatch(r'[0-9a-f]{64}', source[key]) is not None,
                'Invalid source SHA-256: ' + key)
    for key in ('input_lines', 'matched_lines', 'ignored_lines'):
        require(type(source[key]) is int and source[key] >= 0, 'Invalid source line count: ' + key)
    require(source['matched_lines'] + source['ignored_lines'] == source['input_lines'],
            'Matched and ignored lines must equal input lines')
    for key in ('source_path', 'mapping_path'):
        require(isinstance(source[key], str) and bool(source[key]), 'Source path required: ' + key)

    rows = {}
    children = {}
    for row in raw['stages']:
        fields(row, ('id', 'name', 'parent_id', 'inclusive', 'self', 'calls', 'correctness',
                     'children_coverage', 'work', 'source_lines'),
               ('id', 'name', 'parent_id', 'inclusive', 'self', 'calls', 'correctness',
                'children_coverage', 'work', 'source_lines'), 'phase summary stage')
        require(isinstance(row['id'], str) and bool(row['id']), 'Stage ID required')
        require(isinstance(row['name'], str) and bool(row['name']), 'Stage name required')
        require(row['parent_id'] is None or isinstance(row['parent_id'], str), 'Invalid parent_id')
        require(row['id'] not in rows, 'Duplicate phase summary ID')
        require(row['correctness'] in ('passed', 'failed', 'not_checked'), 'Invalid correctness status')
        require(row['children_coverage'] in ('unknown', 'complete_nonoverlapping'),
                'Invalid children_coverage')
        require(row['calls'] is None or (type(row['calls']) is int and row['calls'] >= 0),
                'calls must be a nonnegative integer or null')
        for key in ('inclusive', 'self'):
            value = row[key]
            if value is not None:
                fields(value, ('seconds', 'evidence_state'), ('seconds', 'evidence_state'), key)
                _number(value['seconds'], key + '.seconds')
                require(value['evidence_state'] in _STATES, 'Invalid ' + key + ' evidence state')
        if row['inclusive'] is not None and row['self'] is not None:
            require(row['self']['seconds'] <= row['inclusive']['seconds'],
                    'Reported self time exceeds inclusive time')
        require(isinstance(row['work'], dict), 'work must be an object')
        for key, value in row['work'].items():
            require(isinstance(key, str) and bool(key), 'Work counter name required')
            fields(value, ('value', 'unit', 'evidence_state'),
                   ('value', 'unit', 'evidence_state'), 'work counter')
            _number(value['value'], 'work value')
            require(isinstance(value['unit'], str) and bool(value['unit']), 'Work unit required')
            require(value['evidence_state'] in _WORK_STATES, 'Invalid work evidence state')
        lines = row['source_lines']
        fields(lines, ('count', 'first', 'last'), ('count', 'first', 'last'), 'source_lines')
        require(type(lines['count']) is int and lines['count'] >= 0, 'Invalid source line count')
        if lines['count'] == 0:
            require(lines['first'] is None and lines['last'] is None,
                    'Empty source range must have null endpoints')
        else:
            require(type(lines['first']) is int and type(lines['last']) is int
                    and lines['first'] > 0 and lines['last'] >= lines['first'],
                    'Invalid source line range')
        rows[row['id']] = row
        children[row['id']] = []

    for row in rows.values():
        parent = row['parent_id']
        if parent is not None:
            require(parent in rows, 'Missing phase summary parent')
            children[parent].append(row['id'])
    for identifier, row in rows.items():
        child_rows = [rows[child_id] for child_id in children[identifier]]
        if (row['children_coverage'] == 'complete_nonoverlapping' and row['inclusive'] is not None
                and child_rows and all(child['inclusive'] is not None for child in child_rows)):
            child_total = sum((Decimal(str(child['inclusive']['seconds'])) for child in child_rows), Decimal(0))
            require(child_total <= Decimal(str(row['inclusive']['seconds'])),
                    'Complete nonoverlapping child time exceeds parent inclusive time')

    paths = {}
    for row in rows.values():
        trail, seen, current = [], set(), row
        while current['id'] not in paths:
            require(current['id'] not in seen, 'Cyclic phase summary ancestry')
            seen.add(current['id'])
            trail.append(current)
            if current['parent_id'] is None:
                break
            current = rows[current['parent_id']]
        prefix = paths.get(current['id'], ())
        for item in reversed(trail):
            prefix += (item['id'],)
            paths[item['id']] = prefix

    source_name = Path(path).name
    metrics, missing, summaries = [], [], []
    correctness_failed = False
    for row in rows.values():
        scope = raw['scope'] + '; phase=' + json.dumps(paths[row['id']], ensure_ascii=False,
                                                       separators=(',', ':'))
        inclusive = row['inclusive']
        self_value = row['self']
        self_method = 'reported' if self_value is not None else 'missing'
        if self_value is None and inclusive is not None and row['children_coverage'] == 'complete_nonoverlapping':
            child_ids = children[row['id']]
            child_values = [rows[item]['inclusive'] for item in child_ids]
            if child_ids and all(value is not None for value in child_values):
                child_total = sum((Decimal(str(value['seconds'])) for value in child_values), Decimal(0))
                parent_total = Decimal(str(inclusive['seconds']))
                require(child_total <= parent_total,
                        'Complete nonoverlapping child time exceeds parent inclusive time')
                self_value = {'seconds': float(parent_total - child_total),
                              'evidence_state': 'Estimated'}
                self_method = 'estimated_complete_children'

        correctness_failed |= row['correctness'] == 'failed'
        contract_base = dict(representation='summary', scope=scope)
        summary = {'id': row['id'], 'name': row['name'], 'parent_id': row['parent_id'],
                   'path': list(paths[row['id']]), 'scope': scope,
                   'inclusive_seconds': inclusive['seconds'] if inclusive else None,
                   'inclusive_evidence_state': inclusive['evidence_state'] if inclusive else 'Missing',
                   'self_seconds': self_value['seconds'] if self_value else None,
                   'self_evidence_state': self_value['evidence_state'] if self_value else 'Missing',
                   'calls': row['calls'], 'calls_evidence_state': 'Measured' if row['calls'] is not None else 'Missing',
                   'correctness': row['correctness'], 'self_method': self_method,
                   'children_coverage': row['children_coverage'], 'source_lines': row['source_lines'],
                   'evidence': source_name, 'work': row['work']}
        summaries.append(summary)
        for metric_id, value, unit in (
                ('phase.inclusive_time', inclusive, 'seconds'),
                ('phase.self_time', self_value, 'seconds'),
                ('phase.calls', {'seconds': row['calls'], 'evidence_state': 'Measured'}
                 if row['calls'] is not None else None, 'count')):
            if value is None:
                missing.append(observation(metric_id, 'Missing', reason='Summary did not report this value',
                                           scope=scope, unit=unit, evidence=source_name))
            else:
                contract = json.dumps({**contract_base, 'kindMetric': metric_id,
                                       **({'children_coverage': row['children_coverage'],
                                           'self_method': self_method} if metric_id == 'phase.self_time' else {})}, ensure_ascii=False,
                                      sort_keys=True, separators=(',', ':'))
                metrics.append(metric(metric_id, value['seconds'], unit, 'target-phase-summary', scope,
                                      source_name, evidence_state=value['evidence_state'],
                                      correctness_status=row['correctness'], counter_contract=contract))
        for key, item in row['work'].items():
            counter_scope = scope + '; counter=' + json.dumps(key, ensure_ascii=False)
            contract = json.dumps({**contract_base, 'kindMetric': 'phase.work', 'counter': key, 'unit': item['unit']},
                                  ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            metrics.append(metric('phase.work', item['value'], item['unit'], 'target-phase-summary',
                                  counter_scope, source_name, evidence_state=item['evidence_state'],
                                  correctness_status=row['correctness'], counter_contract=contract))

    for metric_id, explanation in (
            ('phase.root_wall', 'Summary records contain no root interval'),
            ('phase.covered_wall', 'Summary records contain no interval coverage'),
            ('phase.uncovered_wall', 'Summary records contain no interval complement')):
        missing.append(observation(metric_id, 'Missing', reason=explanation, scope=raw['scope'],
                                   unit='seconds', evidence=source_name))
    unavailable = [observation('thread_timeline', 'Missing', reason='Summary is not scheduler event data',
                               scope=raw['scope'], evidence=source_name)]
    timeline = {'schema_version': 1, 'scope': raw['scope'], 'evidence_state': 'Missing',
                'evidence': source_name, 'tracks': [],
                'os_scheduler': {'evidence_state': 'Missing',
                                 'reason': 'Summary is not scheduler event data'},
                'limitations': ['No event intervals or scheduler records are present.']}
    return {'clock': None, 'metrics': metrics, 'phases': [], 'phase_summaries': summaries,
            'thread_timeline': timeline, 'unavailable_observations': missing + unavailable,
            'correctness_failed': correctness_failed,
            'limitations': ['Caller-reported phase summaries; no interval or wall-clock reconstruction.',
                            'Estimated self time relies on caller-declared complete, nonoverlapping child coverage.',
                            'Source line counts are parser evidence, not invocation counts.'],
            'source': raw['source']}


def normalize_summary(raw, path):
    """Validate and normalize a version-1 summary object at the import boundary."""
    try:
        return _parse(raw, path)
    except LabError as error:
        if error.category != 'invalid_request':
            raise
        raise LabError('parse_failed', str(error), {'evidence': str(path)}) from error
    except (TypeError, ValueError, OverflowError) as error:
        raise LabError('parse_failed', 'Invalid phase summary',
                       {'reason': str(error), 'evidence': str(path)}) from error
