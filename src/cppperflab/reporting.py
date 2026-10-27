"""Export a human report and compact Agent projection from one saved view."""
import json
from pathlib import Path

from .agent_report import build_agent_report
from .errors import LabError
from .html_report import render_html


def _destination(path, store):
    path = Path(path).resolve()
    if path.is_relative_to(store.root):
        raise LabError('invalid_request', 'Export reports outside the authoritative experiment store')
    return path


def _agent_json(report):
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n'


def _manifest(view, **paths):
    return {'schema_version': 1, 'record_id': view['record_id'], 'view_kind': view['view_kind'],
            'status': view['status'], 'error': view['error'], 'paths': paths}


def export_agent(view, store, output, *, limit=5):
    path = _destination(output, store)
    report = build_agent_report(view, store.root, limit=limit)
    content = _agent_json(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return _manifest(view, agent=str(path))


def export_both(view, store, output, *, limit=5):
    directory = _destination(output, store)
    html_path = _destination(directory / 'report.html', store)
    agent_path = _destination(directory / 'report.agent.json', store)
    if html_path == agent_path:
        raise LabError('invalid_request', 'HTML and agent report destinations must be distinct')

    report = build_agent_report(view, store.root, limit=limit)
    document = render_html(view, store, html_path, agent_report=report)
    content = _agent_json(report)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(document, encoding='utf-8')
    agent_path.write_text(content, encoding='utf-8')
    return _manifest(view, html=str(html_path), agent=str(agent_path))
