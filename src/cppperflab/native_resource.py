"""Linux wait4 child-resource report: strict parsing and native metric semantics."""
import json
import os
from pathlib import Path

from .errors import LabError
from .protocol import fields, metric, require

SCOPE = ('Linux wait4 direct child including fork/exec and waited-for descendants; '
         'peak RSS is not simultaneous process-tree RSS or heap usage')
LIMITATIONS = [SCOPE, 'CPU time uses the OS-reported timeval; microsecond fields do not guarantee microsecond resolution.',
               'Resource counts describe the terminated target invocation, including startup.']
RESOURCE_METRICS = {
    'memory.rss_peak': {'domains': ['D04', 'D05'], 'category': 'Outcome', 'unit': 'bytes',
                        'formula': 'Linux wait4 ru_maxrss (KiB) * 1024', 'direction': 'lower', 'source': 'max_rss_kib'},
    'time.cpu.user': {'domains': ['D06', 'D07', 'D09'], 'category': 'Diagnostic', 'unit': 'seconds',
                      'formula': 'ru_utime seconds + microseconds / 1000000', 'direction': 'none', 'source': 'user_time'},
    'time.cpu.system': {'domains': ['D06', 'D07', 'D09'], 'category': 'Diagnostic', 'unit': 'seconds',
                        'formula': 'ru_stime seconds + microseconds / 1000000', 'direction': 'none', 'source': 'system_time'},
    'os.minor_page_faults': {'domains': ['D10'], 'category': 'Diagnostic', 'unit': 'count',
                             'formula': 'Linux wait4 ru_minflt', 'direction': 'none', 'source': 'minor_faults'},
    'os.major_page_faults': {'domains': ['D10'], 'category': 'Diagnostic', 'unit': 'count',
                             'formula': 'Linux wait4 ru_majflt', 'direction': 'none', 'source': 'major_faults'},
}


def _nonnegative(value, name, limit=(1 << 63) - 1):
    require(type(value) is int and 0 <= value <= limit, name + ' must be a nonnegative integer in range')


def parse_resource(path):
    """Parse a helper's report; do not decide whether its target succeeded."""
    path = Path(path)
    try:
        report = json.loads(path.read_text(encoding='utf-8'),
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, ValueError) as error:
        raise LabError('parse_failed', 'Cannot read native resource report', {'evidence': str(path), 'reason': str(error)}) from error
    try:
        keys = ('schema_version', 'collector', 'version', 'child_pid', 'wait_status', 'exited', 'exit_code', 'signal', 'exec_errno', 'rusage')
        fields(report, keys, keys, 'native resource report')
        require(type(report['schema_version']) is int and report['schema_version'] == 1 and report['collector'] == 'linux-wait4'
                and type(report['version']) is int and report['version'] == 1, 'Unsupported native resource report version')
        _nonnegative(report['child_pid'], 'child_pid', (1 << 31) - 1)
        require(report['child_pid'] > 0, 'child_pid must be positive')
        _nonnegative(report['wait_status'], 'wait_status', 65535)
        status = report['wait_status']
        require(type(report['exited']) is bool, 'exited must be boolean')
        if os.WIFEXITED(status):
            require(report['exited'] is True and type(report['exit_code']) is int and report['exit_code'] == os.WEXITSTATUS(status)
                    and report['signal'] is None, 'child exit does not match wait status')
        elif os.WIFSIGNALED(status):
            require(report['exited'] is False and report['exit_code'] is None and type(report['signal']) is int
                    and report['signal'] == os.WTERMSIG(status), 'child signal does not match wait status')
        else:
            require(False, 'wait status must describe terminal child')
        if report['exec_errno'] is not None:
            _nonnegative(report['exec_errno'], 'exec_errno', (1 << 31) - 1)
            require(report['exec_errno'] > 0 and report['exited'] and report['exit_code'] == 127,
                    'exec_errno requires a failed exec exit')
        usage = report['rusage']
        ukeys = ('max_rss_kib', 'user_time', 'system_time', 'minor_faults', 'major_faults',
                 'voluntary_context_switches', 'involuntary_context_switches', 'filesystem_inputs', 'filesystem_outputs')
        fields(usage, ukeys, ukeys, 'rusage')
        for key in ukeys:
            if key in ('user_time', 'system_time'):
                fields(usage[key], ('seconds', 'microseconds'), ('seconds', 'microseconds'), key)
                _nonnegative(usage[key]['seconds'], key + '.seconds')
                _nonnegative(usage[key]['microseconds'], key + '.microseconds', 999999)
            else:
                _nonnegative(usage[key], key)
    except LabError as error:
        raise LabError('parse_failed', str(error), {'evidence': str(path)}) from error
    values = {
        'memory.rss_peak': usage['max_rss_kib'] * 1024,
        'time.cpu.user': usage['user_time']['seconds'] + usage['user_time']['microseconds'] / 1_000_000,
        'time.cpu.system': usage['system_time']['seconds'] + usage['system_time']['microseconds'] / 1_000_000,
        'os.minor_page_faults': usage['minor_faults'], 'os.major_page_faults': usage['major_faults'],
    }
    metrics = [metric(name, values[name], spec['unit'], 'linux-wait4-rusage', SCOPE, path.name,
                      collector='linux-wait4', collector_version=1, source_field=spec['source'])
               for name, spec in RESOURCE_METRICS.items()]
    return {'metrics': metrics, 'hotspots': [], 'unavailable_metrics': [], 'unavailable_observations': [],
            'limitations': LIMITATIONS[:], 'collector': 'linux-wait4', 'collector_version': 1,
            'child_status': {key: report[key] for key in ('child_pid', 'wait_status', 'exited', 'exit_code', 'signal', 'exec_errno')}}
