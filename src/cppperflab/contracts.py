"""Input validation for optional, explicitly versioned experiment contracts.

The user's declarations are not evidence that they hold.  These functions only
validate and normalize configuration; execution and validity are owned elsewhere.
"""
from pathlib import Path
import re

from .errors import LabError


def _version(value, name):
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise LabError('unsupported_schema', 'Only ' + name + ' schema_version=1 is supported')


def _text(value):
    return isinstance(value, str) and bool(value)


def validate_claim(value):
    """Validate one claim without treating its hypothesis as verified."""
    from .protocol import fields, require

    required = ('schema_version', 'hypothesis', 'kind', 'applies_to',
                'allowed_changes', 'metric', 'conditions')
    fields(value, (*required, 'metric_scope'), required, 'claim')
    _version(value, 'claim')
    require(all(_text(value[key]) for key in ('hypothesis', 'applies_to', 'metric')),
            'claim hypothesis, applies_to and metric must be nonempty text')
    require(value['kind'] in ('task_cost', 'unit_efficiency', 'mechanism_hypothesis'),
            'Invalid claim kind')
    require('metric_scope' not in value or _text(value['metric_scope']),
            'claim metric_scope must be nonempty text')
    changes = value['allowed_changes']
    require(isinstance(changes, list) and all(_text(item) for item in changes)
            and len(set(changes)) == len(changes),
            'claim allowed_changes must contain distinct nonempty strings')
    conditions = value['conditions']
    require(isinstance(conditions, list), 'claim conditions must be an array')
    ids = set()
    for condition in conditions:
        fields(condition, ('id', 'description', 'source', 'required', 'metric', 'metric_scope'),
               ('id', 'description', 'source', 'required'), 'claim condition')
        require(_text(condition['id']) and _text(condition['description']),
                'claim condition id and description must be nonempty text')
        require(condition['id'] not in ids, 'claim condition ids must be distinct')
        ids.add(condition['id'])
        require(condition['source'] in ('declaration', 'correctness', 'execution_state', 'work_metric'),
                'Invalid claim condition source')
        require(type(condition['required']) is bool, 'claim condition required must be boolean')
        if condition['source'] == 'work_metric':
            require('metric' in condition and _text(condition['metric']),
                    'work_metric condition requires a metric')
            require('metric_scope' not in condition or _text(condition['metric_scope']),
                    'work_metric metric_scope must be nonempty text')
        else:
            require('metric' not in condition and 'metric_scope' not in condition,
                    'Only work_metric conditions accept metric or metric_scope')
    if value['kind'] == 'unit_efficiency':
        require(any(item['source'] == 'work_metric' and item['required'] for item in conditions),
                'unit_efficiency requires a required work_metric condition')
    return value


def validate_execution_state(value, base):
    """Normalize explicit trusted action executables without running them."""
    from .protocol import argv, executable, fields, require

    required = ('schema_version', 'scope', 'process', 'object_state',
                'cache', 'external_state', 'required')
    actions = ('setup', 'reset', 'verify', 'cleanup')
    fields(value, (*required, *actions), required, 'execution_state')
    _version(value, 'execution_state')
    require(value['scope'] in ('measurement_invocation', 'measurement_sample'),
            'Invalid execution_state scope')
    require(value['process'] == 'fresh_process', 'Invalid execution_state process')
    require(_text(value['object_state']) and _text(value['external_state']),
            'execution_state object_state and external_state must be nonempty text')
    require(value['cache'] in ('uncontrolled', 'cold', 'warm'), 'Invalid execution_state cache')
    require(type(value['required']) is bool, 'execution_state required must be boolean')
    for name in actions:
        if name in value:
            action = value[name]
            fields(action, ('argv',), ('argv',), 'execution_state ' + name)
            argv(action['argv'], 'execution_state ' + name + '.argv')
            action['argv'][0] = executable(action['argv'][0], Path(base))
    return value


def validate_workflow(value):
    """Validate Study stage/source declarations; provenance checks run later."""
    from .protocol import fields, require

    required = ('schema_version', 'phase', 'source_study_ids', 'selection_reason')
    fields(value, (*required, 'selected_run_id'), required, 'workflow')
    _version(value, 'workflow')
    require(value['phase'] in ('exploration', 'calibration', 'confirmation', 'acceptance'),
            'Invalid workflow phase')
    sources = value['source_study_ids']
    require(isinstance(sources, list) and all(_text(item) for item in sources)
            and len(set(sources)) == len(sources),
            'workflow source_study_ids must contain distinct nonempty strings')
    require(_text(value['selection_reason']), 'workflow selection_reason must be nonempty text')
    if value['phase'] in ('confirmation', 'acceptance'):
        require(bool(sources), 'confirmation and acceptance require source studies')
        selected=value.get('selected_run_id')
        require(isinstance(selected,str) and re.fullmatch(r'\d{8}T\d{6}-[0-9a-f]{12}',selected),
                'confirmation and acceptance require a valid selected_run_id')
    else:
        require('selected_run_id' not in value,
                'selected_run_id is only supported for confirmation or acceptance')
    return value
