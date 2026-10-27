"""Linux generic cache ABI definitions and post-collection context evidence.

The generic Linux selector describes a kernel API request. It does not reveal
which vendor event encoding the kernel mapped onto a particular physical PMU.
"""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import platform
import re

from .storage import write_json


def _alias(metric_id, name, cache, cache_code, result, result_code):
    return {'metric_id': metric_id, 'name': name,
            'linux_abi': {'type': 3, 'config': cache_code | (result_code << 16),
                          'cache': cache, 'operation': 'read', 'result': result},
            'description': f'Linux generic {cache} read {result}; kernel-to-PMU mapping is not exposed'}


CACHE_ALIASES = {
    'L1-dcache-loads': _alias('cpu.l1d_loads', 'L1 data cache read accesses', 'L1D', 0, 'access', 0),
    'L1-dcache-load-misses': _alias('cpu.l1d_load_misses', 'L1 data cache read misses', 'L1D', 0, 'miss', 1),
    'LLC-loads': _alias('cpu.llc_loads', 'Last-level cache read accesses', 'LL', 2, 'access', 0),
    'LLC-load-misses': _alias('cpu.llc_load_misses', 'Last-level cache read misses', 'LL', 2, 'miss', 1),
    'dTLB-loads': _alias('cpu.dtlb_loads', 'Data TLB read accesses', 'DTLB', 3, 'access', 0),
    'dTLB-load-misses': _alias('cpu.dtlb_load_misses', 'Data TLB read misses', 'DTLB', 3, 'miss', 1),
}
METRIC_SELECTORS = {row['metric_id']: selector for selector, row in CACHE_ALIASES.items()}
DESCRIPTOR_FIELDS = ('event_descriptor_id', 'event_descriptor_version', 'event_semantics_key')


def descriptor_problem(item):
    """Check the new observation contract without upgrading legacy counters."""
    selector = METRIC_SELECTORS.get(item['name'])
    if selector is None:
        return None
    if (item.get('event_descriptor_id') != 'linux_hw_cache_alias:' + selector
            or type(item.get('event_descriptor_version')) is not int
            or item['event_descriptor_version'] != 1
            or not isinstance(item.get('event_semantics_key'), str)
            or re.fullmatch('[0-9a-f]{64}', item['event_semantics_key']) is None
            or not item.get('event_descriptor_evidence')
            or item.get('event', '').partition(':')[0] != selector):
        return 'Cache/TLB observations require matching event descriptors and raw selectors.'
    return None

CPUINFO_PATH = Path('/proc/cpuinfo')
PMU_ROOT = Path('/sys/bus/event_source/devices')
_CPU_FIELDS = {'vendor_id': 'vendor', 'cpu family': 'family', 'model': 'model',
               'stepping': 'stepping', 'model name': 'model_name', 'microcode': 'microcode'}


def descriptor_definition(selector):
    """Return a standalone static ABI definition; never inspect the host."""
    return deepcopy(CACHE_ALIASES[selector])


def _missing():
    return {'evidence_state': 'Missing', 'reason': 'not available'}


def _read_file(path):
    try:
        return {'evidence_state': 'Measured', 'value': path.read_text(encoding='utf-8').strip()}
    except (OSError, UnicodeError):
        return _missing()


def _read_directory(path):
    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name)
    except OSError:
        return _missing()
    return {'evidence_state': 'Measured', 'values': {item.name: _read_file(item) for item in entries if item.is_file()}}


def _context(directory, perf_version):
    try:
        cpuinfo = CPUINFO_PATH.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        cpuinfo = ''
        source_status = _missing()
    else:
        source_status = {'evidence_state': 'Measured'}
    values = {name: set() for name in _CPU_FIELDS.values()}
    fragments = []
    for line in cpuinfo.splitlines():
        key, sep, value = line.partition(':')
        key = key.strip()
        if sep and (key in _CPU_FIELDS or key == 'processor'):
            fragments.append(line)
            if key in _CPU_FIELDS:
                values[_CPU_FIELDS[key]].add(value.strip())
    identity = {key: {'evidence_state': 'Measured', 'values': sorted(items)} if items else _missing()
                for key, items in values.items()}
    (directory / 'cpuinfo-context.txt').write_text('\n'.join(fragments) + ('\n' if fragments else ''), encoding='utf-8')
    try:
        devices = sorted((p for p in PMU_ROOT.iterdir() if p.name == 'cpu' or p.name.startswith('cpu_')), key=lambda p: p.name)
    except OSError:
        inventory = {**_missing(), 'candidates': []}
    else:
        inventory = {'evidence_state': 'Measured', 'candidates': [
            {'name': device.name, 'type': _read_file(device / 'type'),
             'caps': _read_directory(device / 'caps'), 'format': _read_directory(device / 'format'),
             'cpus': _read_file(device / 'cpus'), 'cpumask': _read_file(device / 'cpumask')}
            for device in devices]}
    write_json(directory / 'pmu-inventory.json', inventory)
    return {'machine': platform.machine(), 'kernel': platform.release(), 'perf_version': perf_version,
            'perf_version_state': 'Missing' if perf_version is None else 'Measured',
            'cpuinfo_source': source_status, 'cpu_identity': identity, 'pmu_inventory': inventory,
            'cpuinfo_evidence': 'cpuinfo-context.txt', 'pmu_inventory_evidence': 'pmu-inventory.json',
            'capture_note': 'Captured after collection; CPU identity and PMU candidates do not identify the physical CPU or PMU used for this measurement.'}


def attach_descriptors(directory, parsed, perf_version, selectors):
    """Attach descriptors to matching observations and archive post-collection context."""
    selected = set(selectors) & CACHE_ALIASES.keys()
    if not selected:
        return None
    directory = Path(directory)
    context = _context(directory, perf_version)
    rows = [(item, True) for item in parsed['metrics']] + [(item, False) for item in parsed.get('unavailable_observations', [])]
    present = {(item.get('raw_event') or item['event']).partition(':')[0] for item, _ in rows}
    rows.extend(({'event': selector}, False) for selector in sorted(selected - present))
    events = []
    for item, measured in rows:
        raw = item.get('raw_event')
        if raw is None and measured:
            raw = item.get('event')
        observed = raw if raw is not None else None
        selector = (raw if raw is not None else item['event']).partition(':')[0]
        if selector not in selected:
            continue
        definition = descriptor_definition(selector)
        filters = ({'evidence_state': 'Measured', 'value': observed.partition(':')[2]} if observed is not None
                   else _missing())
        descriptor_id = 'linux_hw_cache_alias:' + selector
        physical = {'evidence_state': 'Missing', 'reason': 'kernel translation not exposed'}
        semantics = {'event_descriptor_id': descriptor_id, 'event_descriptor_version': 1,
                     'linux_abi': definition['linux_abi'], 'filters': filters,
                     'machine': context['machine'], 'kernel': context['kernel'], 'perf_version': perf_version,
                     'cpuinfo_source': context['cpuinfo_source'], 'cpu_identity': context['cpu_identity'],
                     'pmu_inventory': context['pmu_inventory'], 'physical_mapping': physical}
        key = hashlib.sha256(json.dumps(semantics, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
        row = {'requested_selector': selector, 'observed_selector': observed, 'origin': 'linux_hw_cache_alias',
               'metric_id': definition['metric_id'],
               'linux_abi': definition['linux_abi'], 'filters': filters, 'physical_mapping': physical,
               'event_descriptor_id': descriptor_id, 'event_descriptor_version': 1, 'event_semantics_key': key,
               'evidence': [name for name in ('perf.csv', 'execution.json', 'stderr.txt', 'cpuinfo-context.txt', 'pmu-inventory.json')
                            if (directory/name).is_file()]}
        events.append(row)
        item.update(event_descriptor_id=descriptor_id, event_descriptor_version=1,
                    event_semantics_key=key, event_descriptor_evidence='event-descriptors.json')
    document = {'schema_version': 1, 'context': context, 'events': events}
    write_json(directory / 'event-descriptors.json', document)
    return document
