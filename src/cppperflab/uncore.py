"""Read-only Linux memory-controller metadata discovery; no perf collection."""
import errno
import math
import os
from pathlib import Path
import re

from .storage import write_json

PMU_ROOT = Path('/sys/bus/event_source/devices')
CPU_ROOT = Path('/sys/devices/system/cpu')
CPUINFO_PATH = Path('/proc/cpuinfo')
STATUS_PATH = Path('/proc/self/status')
PARANOID_PATH = Path('/proc/sys/kernel/perf_event_paranoid')
_UNIT_BYTES = {'byte', 'B', 'bytes', 'KiB', 'MiB', 'GiB', 'kB', 'MB', 'GB'}


def _read(path, directory, relative):
    try:
        raw = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        return {'evidence_state':'Missing', 'source':str(path), 'reason':str(error),
                'errno':getattr(error, 'errno', None)}
    evidence = Path('raw') / relative
    destination = directory / evidence
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(raw, encoding='utf-8')
    return {'evidence_state':'Measured', 'source':str(path), 'evidence':str(evidence), 'value':raw.strip()}


def _listing(path):
    try:
        return sorted(path.iterdir(), key=lambda p:p.name), None
    except OSError as error:
        return [], {'evidence_state':'Missing', 'source':str(path), 'reason':str(error), 'errno':error.errno}


def _directory(path, directory, relative):
    entries, error = _listing(path)
    if error is not None:
        return {**error, 'entries':{}}
    rows = {item.name: _read(item,directory,relative/item.name) for item in entries if item.is_file()}
    return {'evidence_state':'Measured', 'source':str(path), 'entries':rows}


def _integer(source, *, nonnegative=True):
    if source['evidence_state'] != 'Measured':
        return {'evidence_state':'Missing', 'reason':'source unavailable'}
    try:
        value = int(source['value'], 0)
    except ValueError:
        return {'evidence_state':'Invalid', 'reason':'not an integer'}
    if nonnegative and value < 0:
        return {'evidence_state':'Invalid', 'reason':'negative integer'}
    return {'evidence_state':'Measured','value':value}


def _format_fields(raw):
    if raw['evidence_state'] != 'Measured':
        return {'evidence_state':'Missing','reason':'format metadata unavailable'}
    fields = {}
    for name, item in raw['entries'].items():
        if item['evidence_state'] != 'Measured':
            fields[name]={'evidence_state':'Missing','reason':'format field unreadable'}
            continue
        match = re.fullmatch(r'(config|config1|config2):([0-9,\-]+)', item['value'])
        bits = []
        if match:
            for part in match.group(2).split(','):
                bounds=part.split('-')
                if len(bounds)>2 or not all(x.isdigit() for x in bounds):
                    match=None;break
                start=int(bounds[0]); end=int(bounds[-1])
                if start > end or end > 63 or any(bit in bits for bit in range(start,end+1)):
                    match=None;break
                bits.extend(range(start,end+1))
        fields[name] = ({'evidence_state':'Measured','register':match.group(1),'bits':bits} if match and bits
                        else {'evidence_state':'Invalid','reason':'malformed format bit fields'})
    states={field['evidence_state'] for field in fields.values()}
    if 'Invalid' in states:
        return {'evidence_state':'Invalid','reason':'one or more PMU format fields are malformed','fields':fields}
    if 'Missing' in states:
        return {'evidence_state':'Missing','reason':'one or more PMU format fields are unreadable','fields':fields}
    return {'evidence_state':'Measured','fields':fields}


def _encoding(raw, format_fields):
    if raw['evidence_state'] != 'Measured':
        return {'evidence_state':'Missing','reason':'event encoding unavailable'}
    if 'fields' not in format_fields:
        return {'evidence_state':format_fields['evidence_state'],'reason':'PMU format unavailable or invalid'}
    values={}
    parameterized=[]
    for component in raw['value'].split(','):
        name, separator, encoded = component.strip().partition('=')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name) or name in values:
            return {'evidence_state':'Invalid','reason':'malformed event encoding'}
        field=format_fields['fields'].get(name)
        if field is None:
            return {'evidence_state':'Invalid','reason':'event field is absent from PMU format: '+name}
        if field['evidence_state'] != 'Measured':
            return {'evidence_state':field['evidence_state'],'reason':'event field format unavailable or invalid: '+name}
        if not separator:
            values[name]={'syntax':'bare'}
            continue
        if encoded == '?':
            values[name]={'syntax':'parameter'}
            parameterized.append(name)
            continue
        try:
            value=int(encoded,0)
        except ValueError:
            return {'evidence_state':'Invalid','reason':'event field is not a literal integer: '+name}
        if value < 0 or value >= (1 << len(field['bits'])):
            return {'evidence_state':'Invalid','reason':'event field value exceeds PMU format: '+name}
        values[name]=value
    if parameterized:
        return {'evidence_state':'Missing','reason':'event parameters require explicit values: '+', '.join(parameterized),'fields':values}
    return {'evidence_state':'Measured','fields':values}


def _unit(raw):
    if raw['evidence_state']!='Measured':return {'evidence_state':'Missing','reason':'unit is not provided'}
    unit=raw['value']
    if not unit:return {'evidence_state':'Invalid','reason':'empty unit'}
    if unit not in _UNIT_BYTES:return {'evidence_state':'Unsupported','reason':'unrecognized unit conversion','value':unit}
    return {'evidence_state':'Measured','value':unit}


def _scale(raw):
    if raw['evidence_state']!='Measured':return {'evidence_state':'Missing','reason':'scale is not provided'}
    try: value=float(raw['value'])
    except ValueError:return {'evidence_state':'Invalid','reason':'scale is not a number'}
    if not math.isfinite(value) or value<=0:return {'evidence_state':'Invalid','reason':'scale must be finite and positive'}
    return {'evidence_state':'Measured','value':value}


def _topology(mask, directory, prefix):
    if mask['evidence_state']!='Measured':
        return {'evidence_state':'Missing','reason':'representative CPU list unavailable'}
    text=mask['value']
    if not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*',text):
        return {'evidence_state':'Invalid','reason':'representative CPU list is not a CPU list'}
    ranges=[tuple(map(int,part.split('-'))) for part in text.split(',')]
    if any(bounds[0]>bounds[-1] for bounds in ranges):
        return {'evidence_state':'Invalid','reason':'representative CPU range is reversed'}
    cpu=ranges[0][0]
    base=CPU_ROOT/('cpu'+str(cpu))
    package=_read(base/'topology/physical_package_id',directory,prefix/'topology/package')
    die=_read(base/'topology/die_id',directory,prefix/'topology/die')
    entries,error=_listing(base)
    nodes=sorted(int(item.name[4:]) for item in entries if re.fullmatch(r'node\d+',item.name))
    node=error if error is not None else {'evidence_state':'Missing','source':str(base),'reason':'no NUMA node visible'} if not nodes else {'evidence_state':'Measured','values':nodes}
    return {'evidence_state':'Measured','representative_cpu':cpu,
            'package':{'raw':package,'parsed':_integer(package)},'die':{'raw':die,'parsed':_integer(die)},'nodes':node,
            'limitation':'Representative CPU metadata only; does not establish PMU scope or complete channel coverage.'}


def _candidate(device, directory):
    prefix=Path('sysfs')/device.name
    raw_type=_read(device/'type',directory,prefix/'type')
    fmt=_directory(device/'format',directory,prefix/'format')
    caps=_directory(device/'caps',directory,prefix/'caps')
    formats=_format_fields(fmt)
    raw_events=_directory(device/'events',directory,prefix/'events')
    names={name.removesuffix('.unit').removesuffix('.scale') for name in raw_events['entries']}
    events=[]
    for name in sorted(names):
        entries=raw_events['entries']
        def missing(field):
            path=device/'events'/field
            return {'evidence_state':'Missing','source':str(path),'reason':'file not visible','errno':errno.ENOENT}
        encoding=entries.get(name,missing(name));unit=entries.get(name+'.unit',missing(name+'.unit'));scale=entries.get(name+'.scale',missing(name+'.scale'))
        events.append({'name':name,'encoding':encoding,'encoding_validation':_encoding(encoding,formats),
                       'unit':unit,'unit_validation':_unit(unit),'scale':scale,'scale_validation':_scale(scale)})
    mask=_read(device/'cpumask',directory,prefix/'cpumask')
    cpus=_read(device/'cpus',directory,prefix/'cpus')
    return {'name':device.name,'type':{'raw':raw_type,'parsed':_integer(raw_type)},
            'format':fmt,'format_validation':formats,'caps':caps,'events_directory':raw_events,'events':events,
            'cpumask':mask,'cpus':cpus,'representative_topology':_topology(mask,directory,prefix)}


def discover(directory):
    """Archive visible kernel metadata only, without running perf or measuring traffic."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    devices,error=_listing(PMU_ROOT)
    names=[item.name for item in devices]
    candidates=[_candidate(device,directory) for device in devices
                if re.fullmatch(r'(?:uncore_imc|amd_umc|umc)[A-Za-z0-9_]*',device.name)]
    inventory=({**error,'visible_pmus':[],'candidates':[]} if error is not None else
               {'evidence_state':'Measured','source':str(PMU_ROOT),'visible_pmus':names,'candidates':candidates})
    cpuinfo=_read(CPUINFO_PATH,directory,Path('proc/cpuinfo'))
    if cpuinfo['evidence_state']=='Measured':
        flags=[line.partition(':')[2].split() for line in cpuinfo['value'].splitlines() if line.startswith('flags') and ':' in line]
        guest={'evidence_state':'Measured','value':any('hypervisor' in row for row in flags),
               'meaning':'Observed CPU hypervisor flag only; absence does not prove bare metal.'} if flags else {'evidence_state':'Missing','reason':'CPU flags unavailable'}
    else:guest={'evidence_state':'Missing','reason':'CPU information unavailable'}
    status=_read(STATUS_PATH,directory,Path('proc/self/status'))
    caps=next((line.partition(':')[2].strip() for line in status.get('value','').splitlines() if line.startswith('CapEff:')),None)
    cap_eff={'evidence_state':'Measured','value':caps} if caps is not None else {'evidence_state':'Missing','reason':'CapEff unavailable'}
    paranoid=_read(PARANOID_PATH,directory,Path('proc/sys/kernel/perf_event_paranoid'))
    system=os.uname()
    report={'schema_version':1,'execution_performed':False,'collection_available':False,
            'scope':'shared memory controller / socket / system; not target process',
            'host':{'machine':system.machine,'kernel':system.release,'cpuinfo':cpuinfo,'guest_indicator':guest},
            'policy':{'perf_event_paranoid':{'raw':paranoid,'parsed':_integer(paranoid,nonnegative=False)},
                      'process_status':status,'cap_eff':cap_eff,
                      'permissions':{'evidence_state':'Missing','reason':'system-wide perf permission was not probed'}},
            'inventory':inventory,
            'collector':{'evidence_state':'Unsupported','reason':'collector_not_implemented'},
            'measurement':{'evidence_state':'Unsupported','value':None,
                'reason':'inventory_unavailable' if error is not None else 'collector_not_implemented' if candidates else 'no_visible_candidate'},
            'limitations':['Only metadata visible in this namespace is inventoried; absent devices do not establish physical absence.',
                'No perf, tool, target or system-wide permission probe command is executed; no traffic collection is performed.',
                'Controller/socket traffic includes background activity and cannot be attributed directly to a target process.',
                'CPU masks do not establish complete channel coverage or the physical scope of a PMU.',
                'No event unit or scale is assumed and no byte conversion is performed.']}
    write_json(directory/'uncore-readiness.json',report)
    return report


def probe(store):
    identifier,directory=store.create('probes')
    result={'schema_version':1,'probe_id':identifier,'evidence_directory':str(directory),
            'capabilities':{'memory_controller_traffic':discover(directory)}}
    write_json(directory/'probe.json',result)
    return result
