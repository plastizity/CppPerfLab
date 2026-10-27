"""Caller-defined completed work reported for one target invocation."""
import json
import math
from pathlib import Path

from .errors import LabError
from .protocol import fields, metric, require

METRIC_IDS = ('work.items',)
WORK_REPORT_TOOLS = ('perf-stat', 'native-resource')
LIMITATION = ('Work definition, correctness and completeness are declared by the target; '
              'the consumer must validate them and cross-run equivalence.')


def validate_config(config):
    fields(config, ('schema_version', 'path', 'definition_id'),
           ('schema_version', 'path', 'definition_id'), 'work_report')
    require(type(config['schema_version']) is int and config['schema_version'] == 1,
            'Unsupported work_report version')
    path = config['path']
    require(isinstance(path, str) and bool(path) and Path(path).name == path and
            path.endswith('.work.json') and '\x00' not in path,
            'work_report.path must be a *.work.json filename in the measurement directory')
    definition = config['definition_id']
    require(isinstance(definition, str) and bool(definition.strip()),
            'work_report.definition_id must be nonempty')


def normalize(path, config):
    """Validate a fresh invocation report and return its measured work metric."""
    try:
        report = json.loads(Path(path).read_text(encoding='utf-8'),
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, ValueError) as error:
        raise LabError('parse_failed', 'Cannot read target work report',
                       {'evidence': str(path), 'reason': str(error)}) from error
    try:
        fields(report, ('schema_version', 'definition_id', 'unit', 'count', 'scope', 'count_kind', 'completed'),
               ('schema_version', 'definition_id', 'unit', 'count', 'scope', 'count_kind', 'completed'), 'work report')
        require(type(report['schema_version']) is int and report['schema_version'] == 1, 'Unsupported work report version')
        require(report['definition_id'] == config['definition_id'], 'Work definition_id does not match the request')
        require(isinstance(report['unit'], str) and bool(report['unit'].strip()), 'Work unit must be nonempty')
        count = report['count']
        require(type(count) is int and count >= 0, 'Work count must be a nonnegative integer')
        try:
            finite = math.isfinite(count)
        except OverflowError:
            finite = False
        require(finite, 'Work count must be finite')
        require(report['scope'] == 'target-invocation' and report['count_kind'] == 'observed' and report['completed'] is True,
                'Work must describe observed, completed work for this target invocation')
    except LabError as error:
        raise LabError('parse_failed', str(error), {'evidence': str(path)}) from error
    definition = report['definition_id']
    unit = report['unit']
    return metric('work.items', count, unit, 'target-reported-invocation-work',
                  'target invocation; completed business work', Path(path).name,
                  capture_scope='target_process_and_inherited_threads',
                  capture_window='whole-target-invocation', work_definition_id=definition,
                  work_unit=unit, count_kind='observed', completed=True,
                  counter_contract=json.dumps({'definition_id': definition, 'unit': unit,
                      'scope': report['scope'], 'count_kind': report['count_kind'], 'completed': True},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=False))
