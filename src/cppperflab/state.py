"""Consumer-owned execution-state actions at existing measurement boundaries.

An action's exit status is an execution fact. Only an explicit verifier can
report a state check, and its result covers only its declared observation.
"""
import json
from pathlib import Path

from .errors import LabError


def state_actions(request):
    state = request.get('execution_state')
    if state is None:
        return []
    return [(name, state[name]['argv']) for name in ('setup', 'reset', 'verify') if name in state]


def state_budget(request, measurement_invocations):
    state = request.get('execution_state')
    if state is None:
        return None
    counts = {name: measurement_invocations if name in state else 0
              for name in ('setup', 'reset', 'verify', 'cleanup')}
    total = sum(counts.values())
    return {'scope': state['scope'], 'controlled_invocations': measurement_invocations,
            'action_commands': counts, 'additional_commands': total,
            'additional_target_computation_runs': None if total else 0,
            'execution_performed': False,
            'limitations': [
                'Actions are consumer-provided trusted commands; their internal work and side effects are unknown.',
                'State actions are outside target timing and may affect the measured execution.',
                'A verifier PASS covers only what that consumer-provided verifier checks.',
                'A batch invocation boundary does not establish the state of each internal repetition.']}


def _references(directory):
    # The caller places the directory directly below the Run root. Persist only
    # files that the existing launch boundary actually created.
    return [str(Path(directory.parent.name) / directory.name / name)
            for name in ('execution.json', 'stdout.txt', 'stderr.txt')
            if (directory / name).is_file()]


def _action(name, command, directory, launch):
    folder = directory / name
    try:
        record = launch(command, folder, execution_stage='state_' + name)
    except LabError as error:
        action = {'name': name, 'status': 'FAIL' if error.category == 'target_failed' else 'INCONCLUSIVE',
                  'execution': None, 'error': error.as_dict(), 'evidence_refs': _references(folder)}
        execution = folder / 'execution.json'
        if execution.is_file():
            action['execution'] = json.loads(execution.read_text(encoding='utf-8'))
        raise LabError(error.category, str(error), {**error.details, 'state_action': action}) from error
    return {'name': name, 'status': 'PASS', 'execution': record, 'evidence_refs': _references(folder)}


def _verification(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'),
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        # Reject non-finite numbers anywhere in consumer-provided observations.
        json.dumps(value, allow_nan=False)
    except (OSError, ValueError) as error:
        raise LabError('parse_failed', 'Cannot read state verifier JSON', {'reason': str(error)}) from error
    if (type(value) is not dict or set(value) - {'schema_version', 'status', 'description', 'observations'}
            or not {'schema_version', 'status', 'description'} <= set(value)
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['status'] not in ('PASS', 'FAIL', 'INCONCLUSIVE')
            or not isinstance(value['description'], str) or not value['description']
            or ('observations' in value and type(value['observations']) is not dict)):
        raise LabError('parse_failed', 'Invalid state verifier JSON')
    return value


def execute_state(request, directory, launch):
    """Execute configured preparation/verification once before a tool invocation.

    `directory` is a unique direct child of the owning Run's directory. A
    missing verifier remains INCONCLUSIVE. An explicitly failed/inconclusive
    verifier stops the formal invocation while preserving its evidence.
    """
    state = request['execution_state']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 1, 'status': 'INCONCLUSIVE', 'scope': state['scope'],
              'actions': [], 'verification': None, 'evidence_refs': [],
              'limitations': ['State declarations and successful actions do not establish state equivalence.',
                              'Verifier results cover only the consumer-provided check.',
                              'Action costs are outside the target timing and can affect the subsequent measurement.']}
    try:
        for name, command in state_actions(request):
            try:
                action = _action(name, command, directory, launch)
            except LabError as error:
                result['actions'].append(error.details['state_action'])
                result['evidence_refs'].extend(error.details['state_action']['evidence_refs'])
                raise
            result['actions'].append(action)
            result['evidence_refs'].extend(action['evidence_refs'])
            if name == 'verify':
                verification = _verification(directory / name / 'stdout.txt')
                result['verification'] = verification
                result['status'] = verification['status']
                if verification['status'] != 'PASS':
                    raise LabError('incomparable', 'Consumer state verification did not pass',
                                   {'acceptance': {'status': verification['status'],
                                                   'reason': verification['description']}})
    except LabError as error:
        if result['status'] != 'FAIL':
            result['status'] = ('FAIL' if error.category == 'target_failed' else
                                error.details.get('acceptance', {}).get('status', 'INCONCLUSIVE'))
        raise LabError(error.category, str(error),
                       {**error.details, 'execution_state_evidence': result}) from error
    return result


def cleanup_state(request, directory, launch):
    """Run an explicitly configured cleanup once; caller preserves primary error."""
    state = request['execution_state']
    if 'cleanup' not in state:
        return None
    try:
        action = _action('cleanup', state['cleanup']['argv'], Path(directory), launch)
    except LabError as error:
        action = error.details['state_action']
        cleanup = {'schema_version': 1, 'status': action['status'], 'action': action,
                   'evidence_refs': action['evidence_refs']}
        raise LabError(error.category, str(error),
                       {**error.details, 'execution_state_cleanup': cleanup}) from error
    return {'schema_version': 1, 'status': 'PASS', 'action': action,
            'evidence_refs': action['evidence_refs']}
