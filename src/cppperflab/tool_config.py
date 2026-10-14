"""Project-local tool locations; no installation or implicit system mutation."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path

from .errors import LabError
from .protocol import fields, require


_active = ContextVar('cppperflab_tool_configuration', default=None)


def load_tool_config(path, registry):
    path = Path(path).resolve()
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise LabError('invalid_request', 'Cannot read tool configuration', {'reason': str(error)}) from error
    fields(value, ('schema_version', 'tools'), ('schema_version', 'tools'), 'tool configuration')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise LabError('unsupported_schema', 'Only tool configuration version 1 is supported')
    require(isinstance(value['tools'], dict), 'tools must be an object')
    require(set(value['tools']) <= set(registry), 'Unknown tool IDs in configuration')
    resolved = {}
    for tool_id, settings in value['tools'].items():
        field = 'executable' if registry[tool_id].executable else 'build_prefix'
        fields(settings, (field,), (field,), 'tool '+tool_id)
        raw = settings[field]
        require(isinstance(raw, str) and raw and '\x00' not in raw, 'Tool path must be a nonempty path string')
        resolved[tool_id] = {field: str((path.parent / raw).resolve())}
    return {'schema_version': 1, 'path': str(path), 'tools': resolved}


@contextmanager
def tool_configuration(path, registry):
    """Each CLI/API operation owns its explicit configuration scope."""
    configuration = load_tool_config(path, registry) if path is not None else None
    token = _active.set(configuration)
    try:
        yield configuration
    finally:
        _active.reset(token)


def configured_tool(tool_id):
    config = _active.get()
    return config['tools'].get(tool_id) if config is not None else None


def configuration_status():
    config = _active.get()
    if config is None:
        return {'source': 'environment/PATH', 'tools': {},
                'precedence': ['explicit config', 'per-tool environment override', 'tool directory/PATH']}
    tools = {}
    for tool_id, settings in config['tools'].items():
        field, raw = next(iter(settings.items()))
        path = Path(raw)
        valid = (path.is_file() and os.access(path, os.X_OK)) if field == 'executable' else path.is_dir()
        tools[tool_id] = {**settings, 'path_usable': valid,
                          'reason': None if valid else 'configured_path_missing_or_not_usable',
                          'meaning': 'Path check only; collection requires the adapter probe.' if field == 'executable'
                          else 'Build hint only; runtime collection requires an integrated benchmark target.'}
    return {'source': config['path'], 'tools': tools,
            'precedence': ['explicit config', 'per-tool environment override', 'tool directory/PATH']}
