"""Native Valgrind profiles. Instrumented observations and models stay distinct."""
import json
import re
from pathlib import Path

from .errors import LabError
from .protocol import metric

CACHE_EVENTS = {
    'Ir':'instructions', 'I1mr':'i1_misses', 'ILmr':'ll_instruction_misses',
    'Dr':'data_reads', 'D1mr':'d1_read_misses', 'DLmr':'ll_read_misses',
    'Dw':'data_writes', 'D1mw':'d1_write_misses', 'DLmw':'ll_write_misses',
    'Bc':'conditional_branches', 'Bcm':'conditional_branch_misses',
    'Bi':'indirect_branches', 'Bim':'indirect_branch_misses',
}
CACHE_RATIOS = {
    'd1_miss_rate': (('D1mr','D1mw'), ('Dr','Dw'), 1),
    'll_data_mpki': (('DLmr','DLmw'), ('Ir',), 1000),
    'conditional_branch_miss_rate': (('Bcm',), ('Bc',), 1),
    'indirect_branch_miss_rate': (('Bim',), ('Bi',), 1),
}
DHAT_FIELDS = {'memory.allocated_bytes':'tb', 'memory.allocations':'tbk',
               'memory.heap_peak_live':'gb', 'memory.heap_end_live':'eb'}


def check(condition, message):
    if not condition:
        raise LabError('parse_failed',message)


def nonnegative_integer(value, name):
    check(type(value) is int and value >= 0, 'Expected nonnegative integer: '+name)
    return value


def parse_cachegrind(path):
    path=Path(path);lines=path.read_text().splitlines()
    events=[line[8:].split() for line in lines if line.startswith('events: ')]
    totals=[line[9:].split() for line in lines if line.startswith('summary: ')]
    check(len(events)==len(totals)==1, 'Expected one Cachegrind event/summary record')
    check(len(events[0])==len(set(events[0])) and set(events[0])==set(CACHE_EVENTS), 'Unsupported Cachegrind event layout')
    check(len(totals[0])==len(events[0]) and all(v.isdigit() for v in totals[0]), 'Incomplete Cachegrind summary')
    counts=dict(zip(events[0],map(int,totals[0])))
    descriptions=[line[6:] for line in lines if line.startswith('desc: ')]
    check(len(descriptions)==3 and all(any(v.startswith(level+' cache:') for v in descriptions) for level in ('I1','D1','LL')),
          'Missing Cachegrind cache model descriptors')
    scope='whole target under Cachegrind; simulated caches/branch predictor'
    items=[metric('cachegrind.'+CACHE_EVENTS[event],value,'count','cachegrind-model',scope,path.name,
                  evidence_state='Modeled',event=event,cache_model=descriptions) for event,value in counts.items()]
    unavailable=[]
    for name,(numerators,denominators,factor) in CACHE_RATIOS.items():
        num=sum(counts[k] for k in numerators);den=sum(counts[k] for k in denominators)
        if name.endswith('miss_rate'):
            check(num<=den, 'Cachegrind misses exceed their references')
        if den==0:
            unavailable.append(dict(name='cachegrind.'+name,value=None,evidence_state='Missing',evidence_version=1,
                                    reason='Zero reference count; ratio undefined',evidence=path.name))
            continue
        items.append(metric('cachegrind.'+name,num/den*factor,
                            'miss/1000 instructions' if name.endswith('mpki') else 'ratio',
                            'cachegrind-model',scope,path.name,evidence_state='Modeled',cache_model=descriptions,
                            sources={k:counts[k] for k in (*numerators,*denominators)}))
    return dict(metrics=items,hotspots=[],unavailable_metrics=[],unavailable_observations=unavailable,
                limitations=['Cachegrind models cache/branch behavior; these are not hardware PMU events or DRAM traffic.',
                             'Instrumentation duration is not native runtime; model descriptors remain in raw evidence.'])


def parse_dhat(path, operation_count=None):
    path=Path(path);raw=json.loads(path.read_text())
    check(isinstance(raw,dict) and {'dhatFileVersion','mode','bklt','pps','ftbl'}<=set(raw), 'Incomplete DHAT header')
    check(raw['dhatFileVersion']==2 and raw['mode']=='heap' and raw['bklt'] is True, 'Expected DHAT v2 heap lifetime profile')
    check(isinstance(raw['pps'],list) and isinstance(raw['ftbl'],list) and all(isinstance(f,str) for f in raw['ftbl']), 'Malformed DHAT program points/frame table')
    sites=[]
    for point in raw['pps']:
        check(isinstance(point,dict) and {'tb','tbk','gb','gbk','eb','ebk','fs'}<=set(point), 'Incomplete DHAT program point')
        for field in ('tb','tbk','gb','gbk','eb','ebk'):
            nonnegative_integer(point[field],field)
        check(point['gb']<=point['tb'] and point['eb']<=point['tb'], 'Invalid DHAT live bytes')
        check(isinstance(point['fs'],list) and all(type(i) is int and 0<=i<len(raw['ftbl']) for i in point['fs']), 'Invalid DHAT frame reference')
        sites.append(dict(allocated_bytes=point['tb'],allocations=point['tbk'],
                          live_bytes_at_global_peak=point['gb'],end_live_bytes=point['eb'],
                          frames=[raw['ftbl'][i] for i in point['fs']],evidence=path.name))
    scope='DHAT tracked heap over whole target; includes runtime allocations; excludes RSS and allocator metadata'
    items=[metric(name,sum(point[field] for point in raw['pps']),
                  'count' if field=='tbk' else 'bytes','dhat-heap-instrumentation',scope,path.name,
                  definition='DHAT allocated blocks, not every allocator API invocation' if field=='tbk' else field)
           for name,field in DHAT_FIELDS.items()]
    if operation_count is not None:
        check(type(operation_count) is int and operation_count>0,'operation_count must be a positive integer')
        for name,field,unit in [('memory.allocations_per_operation','tbk','allocations/operation'),
                                ('memory.allocated_bytes_per_operation','tb','bytes/operation')]:
            items.append(metric(name,sum(p[field] for p in raw['pps'])/operation_count,unit,'dhat-heap-instrumentation',scope,path.name,
                                operation_count=operation_count,normalization='explicit user-defined whole-target operation count'))
    return dict(metrics=items,hotspots=[],unavailable_metrics=[],allocation_sites=sites,
                limitations=['DHAT counts tracked allocated blocks/bytes; allocator and realloc semantics are tool-defined.',
                             'Global heap peak is sum of gb at the same t-gmax, not sum of per-site mb maxima.',
                             'Instrumentation time/access counts are not native allocation time or DRAM bytes.'])


def parse_callgrind(path):
    path=Path(path);lines=path.read_text().splitlines()
    check('# callgrind format' in lines and 'version: 1' in lines, 'Unsupported Callgrind format')
    check([v for v in lines if v.startswith('events:')]==['events: Ir'], 'Expected instruction-only Callgrind events')
    check(len([v for v in lines if v.startswith('part:')])==1, 'Multi-part Callgrind profiles require explicit separate handling')
    totals=[v.split()[1:] for v in lines if v.startswith('totals:')]
    check(len(totals)==1 and len(totals[0])==1 and totals[0][0].isdigit(),'Missing Callgrind totals')
    names={};caller=callee=None;edges=[]
    def symbol(value):
        match=re.fullmatch(r'\((\d+)\)(?: (.+))?',value)
        if match:
            key=int(match[1])
            if match[2] is not None:
                check(key not in names or names[key]==match[2], 'Conflicting compressed function names')
                names[key]=match[2]
            return ('id',key)
        check(bool(value),'Empty Callgrind function name')
        return ('name',value)
    for line in lines:
        if line.startswith('fn='):
            caller=symbol(line[3:]);callee=None
        elif line.startswith('cfn='):
            callee=symbol(line[4:])
        elif line.startswith('calls='):
            fields=line[6:].split()
            check(caller is not None and callee is not None and len(fields)>=2 and fields[0].isdigit(), 'Incomplete Callgrind call association')
            edges.append((caller,callee,int(fields[0])))
    def resolve(value):
        if value[0]=='name':return value[1]
        check(value[1] in names,'Unresolved compressed function reference')
        return names[value[1]]
    resolved=[dict(caller=resolve(a),callee=resolve(b),calls=n,evidence=path.name,
                   evidence_state='Measured',method='callgrind-call-instrumentation') for a,b,n in edges]
    scope='whole target instrumented by Callgrind; machine-level call edges include runtime; source inlining is not a call'
    return dict(metrics=[metric('control.call_count',sum(n for _,_,n in edges),'count','callgrind-call-instrumentation',scope,path.name),
                         metric('callgrind.instructions',int(totals[0][0]),'count','callgrind-instruction-instrumentation',scope,path.name)],
                call_edges=resolved,hotspots=[],unavailable_metrics=[],
                limitations=['Recorded call edges under instrumentation, not a count of inlined source-level calls.',
                             'Guest instruction count is not a native PMU retired-instruction event or cycle count.',
                             'Callgrind execution duration is not native hotspot elapsed time.'])
