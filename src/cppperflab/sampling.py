"""Native perf sample traces and explicit offline ELF attribution; no target reruns."""
from collections import Counter, OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .errors import LabError
from .execution import execute, check_execution
from .protocol import fields, positive, require, sha256
from .storage import write_json


def validate_options(options):
    for key in ('sample_trace',):
        require(key not in options or type(options[key]) is bool,key+' must be boolean')
    require(options.get('call_graph','dwarf') in ('dwarf','fp','none'),'call_graph must be dwarf/fp/none')
    require(options.get('symbolizer','perf') in ('perf','addr2line'),'symbolizer must be perf/addr2line')
    require(options.get('sample_trace',False) or not {'symbolizer','precheck'}&set(options),'Symbolizer/precheck requires sample_trace=true')
    if 'precheck' in options:
        p=options['precheck']
        fields(p,('min_samples','min_resolved_self_fraction','min_resolved_stack_fraction','require_phase_clock'),
               ('min_samples','min_resolved_self_fraction','min_resolved_stack_fraction','require_phase_clock'),'sampling precheck')
        positive(p['min_samples'],'min_samples',integer=True)
        for key in ('min_resolved_self_fraction','min_resolved_stack_fraction'):
            positive(p[key],key,zero=True);require(p[key]<=1,'Sampling fraction must be <= 1')
        require(type(p['require_phase_clock']) is bool,'require_phase_clock must be explicit boolean')


def clock_context():
    try:
        offsets=Path('/proc/self/timens_offsets').read_text()
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        pid_namespace=os.readlink('/proc/self/ns/pid');time_namespace=os.readlink('/proc/self/ns/time')
    except (FileNotFoundError,PermissionError) as error:
        raise LabError('unavailable','Linux clock correlation metadata is unavailable',{'reason':str(error)}) from error
    match=re.search(r'^monotonic\s+(-?\d+)\s+(\d+)\s*$',offsets,re.M)
    if match is None:raise LabError('unavailable','Cannot establish Linux monotonic time namespace offset')
    offset=int(match[1])*1000000000+int(match[2])
    if offset:raise LabError('unavailable','CPU trace correlation requires a zero monotonic namespace offset',{'monotonic_offset_ns':offset})
    return dict(name='CLOCK_MONOTONIC',boot_id=boot_id,
        pid_namespace=pid_namespace,time_namespace=time_namespace,
        monotonic_offset_ns=offset,before_launch_monotonic_ns=time.monotonic_ns())


def command(argv,directory,*,read_output=True):
    record=execute(argv,directory,timeout=60);write_json(Path(directory)/'execution.json',record)
    check_execution(record)
    if record['returncode']:raise LabError('tool_failed','Sampling postprocessor failed',{'argv':argv,'evidence':str(directory)})
    return (Path(directory)/'stdout.txt').read_text() if read_output else None


def helper_evidence(paths,directory):
    result={}
    for name,path in paths.items():
        if path is None:raise LabError('unavailable','Sampling helper missing: '+name)
        version=command([path,'--version'],directory/name)
        result[name]=dict(path=path,sha256=sha256(path),version=version.splitlines()[0],
            source='https://www.gnu.org/software/binutils/',license='GNU GPL; see archived --version output',
            evidence=str(Path('sampling-helpers')/name/'stdout.txt'))
    return result


def static_readiness(target,directory,options,features,paths):
    """Inspect prerequisites without executing the configured target."""
    directory=Path(directory);directory.mkdir()
    issues=[];p=options.get('precheck');method=options.get('call_graph','dwarf')
    try:clock=clock_context()
    except LabError as error:
        if error.category!='unavailable':raise
        clock=None;issues.append(str(error))
    if p and p['min_resolved_stack_fraction']>0:
        if method=='none':issues.append('No call graph was requested')
        if method=='dwarf' and features.get('dwarf_unwind_available') is not True:issues.append('perf DWARF unwinding is not available')
    if p and p['min_resolved_self_fraction']>0 and options.get('symbolizer','perf')=='perf' and features.get('symbolization_available') is not True:
        issues.append('Native perf ELF symbolization is not available; no implicit alternative is selected')
    helpers={}
    if options.get('symbolizer','perf')=='addr2line':
        for name,path in paths.items():
            if path is None:issues.append('Required helper missing: '+name)
        if not issues:helpers=helper_evidence(paths,directory/'sampling-helpers')
    inspection=None
    is_elf=False
    if target is not None:
        with open(target['argv'][0],'rb') as stream:is_elf=stream.read(4)==b'\x7fELF'
    if is_elf and paths.get('readelf') is not None:
        text=command([paths['readelf'],'-h','-S','-n','-W',target['argv'][0]],directory/'target-elf')
        inspection=dict(path=target['argv'][0],sha256=sha256(target['argv'][0]),debug_info='.debug_info' in text,
            symbol_table='.symtab' in text,dynamic_symbols='.dynsym' in text,evidence='target-readiness/target-elf/stdout.txt')
    return dict(status='UNSUPPORTED' if issues else 'PASS',issues=issues,call_graph=method,symbolizer=options.get('symbolizer','perf'),
        target=inspection,helpers=helpers,clock=clock,target_execution_performed=False,
        meaning='Static prerequisites only; actual target sample/stack quality is checked by the explicitly profiled preflight')


HEADER=re.compile(r'^\s*(\d+)/(\d+)\s+(\d+)\.(\d{9}):\s*(.*)$')
SAMPLE=re.compile(r'^(\d+)\s+(cpu-clock(?::[A-Za-z]+)?):\s*(.*)$')
FRAME=re.compile(r'^\s*([0-9a-fA-F]+)\s+(.*?)\s+\(((?:/|\[).*)\)\s*$')


def known_symbol(symbol):
    return symbol not in ('[unknown]','??','') and not re.fullmatch(r'0x[0-9a-fA-F]+',symbol)


def native_samples(path,metadata):
    """Stream one sample at a time; preserve time-local MMAP build identities."""
    pending=None;builds={};index=0
    def frame(line,pid):
        match=FRAME.fullmatch(line)
        if match is None:raise LabError('parse_failed','Malformed perf script frame',{'line':line})
        ip,symbol,dso=match.groups();offset=None
        split=re.fullmatch(r'(.*)\+0x([0-9a-fA-F]+)',dso)
        if split:dso,offset=split[1],int(split[2],16)
        return dict(ip=int(ip,16),native_symbol=symbol,dso=dso,file_offset=offset,recorded_build_id=builds.get((pid,dso)))
    with Path(path).open() as stream:
        for number,line in enumerate(stream,1):
            text=line.rstrip('\n')
            if not text.strip() or text.startswith('#'):continue
            header=HEADER.fullmatch(text)
            if header:
                if pending is not None:
                    if not pending['frames']:raise LabError('parse_failed','CPU sample has no instruction frame')
                    yield pending;pending=None
                pid,tid,seconds,ns,body=header.groups();pid=int(pid);tid=int(tid)
                match=SAMPLE.fullmatch(body)
                if match:
                    period=int(match[1])
                    if period<=0:raise LabError('parse_failed','Nonpositive CPU sample period')
                    pending=dict(index=index,pid=pid,tid=tid,time_ns=int(seconds)*1000000000+int(ns),period=period,event=match[2],
                                 evidence_state='Measured',frames=[],native_line=number);index+=1
                    if match[3]:pending['frames'].append(frame(match[3],pid))
                elif body.startswith('PERF_RECORD_MMAP2 '):
                    mapping=re.fullmatch(r'PERF_RECORD_MMAP2 (\d+)/(\d+): \[.*\]: [rwxps-]{4} (.*)',body)
                    if mapping is None:raise LabError('parse_failed','Malformed perf MMAP2 record',{'line':text})
                    build=re.search(r'<([0-9a-fA-F]+)>',body)
                    builds[(int(mapping[1]),mapping[3])]=build[1].lower() if build else None
                    metadata['mapping_records']+=1
                elif body.startswith('PERF_RECORD_LOST '):
                    lost=re.search(r'\blost\s+(\d+)\b',body)
                    if lost is None:raise LabError('parse_failed','Unrecognized perf loss record',{'line':text})
                    metadata['lost_events']+=int(lost[1])
                elif not body.startswith(('PERF_RECORD_COMM','PERF_RECORD_FORK','PERF_RECORD_EXIT','PERF_RECORD_MMAP ')):
                    raise LabError('parse_failed','Unexpected perf script record',{'line':text})
            elif pending is not None:pending['frames'].append(frame(text,pending['pid']))
            else:raise LabError('parse_failed','Orphan perf script frame',{'line':text})
    if pending is not None:
        if not pending['frames']:raise LabError('parse_failed','CPU sample has no instruction frame')
        yield pending


class ElfResolver:
    def __init__(self,directory,paths,expected):
        self.directory=Path(directory);self.paths=paths;self.expected=expected;self.files={};self.cache=OrderedDict();self.serial=0

    def image(self,path):
        if path in self.files:return self.files[path]
        entry=dict(path=path,evidence_state='Missing',reason='No verified ELF identity',segments=[])
        self.files[path]=entry
        if not path.startswith('/') or path.endswith(' (deleted)'):return entry
        try:
            with open(path,'rb') as stream:magic=stream.read(4)
            if magic!=b'\x7fELF':entry.update(evidence_state='Unsupported',reason='Mapped object is not ELF');return entry
            digest=sha256(path)
        except FileNotFoundError:return entry
        except PermissionError as error:entry.update(evidence_state='Unsupported',reason=str(error));return entry
        folder=self.directory/('image-%04d'%len(self.files));text=command([self.paths['readelf'],'-l','-n','-W',path],folder/'elf')
        match=re.search(r'Build ID:\s*([0-9a-fA-F]+)',text)
        for line in text.splitlines():
            segment=re.match(r'\s*LOAD\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+0x[0-9a-fA-F]+\s+(0x[0-9a-fA-F]+)\s+',line)
            if segment:entry['segments'].append(dict(offset=int(segment[1],16),virtual=int(segment[2],16),bytes=int(segment[3],16)))
        if not entry['segments']:raise LabError('parse_failed','ELF contains no parseable load segments',{'path':path})
        entry.pop('reason',None)
        entry.update(evidence_state='Measured',sha256=digest,expected_sha256=self.expected.get(path),build_id=match[1].lower() if match else None,folder=folder,
                     evidence=str(folder.relative_to(self.directory.parent)/'elf/stdout.txt'))
        return entry

    def resolve(self,frames):
        queries={}
        for f in frames:
            key=(f['dso'],f['file_offset'],f['recorded_build_id'])
            if key in self.cache:
                f.update(self.cache[key]);self.cache.move_to_end(key);continue
            image=self.image(f['dso']);answer=dict(symbol='[unknown]',resolved=False,attribution_state='Missing',symbol_source='addr2line')
            if image['evidence_state']!='Measured':answer['reason']=image['reason']
            elif (f['dso'] in self.expected and self.expected[f['dso']]!=image['sha256']) or (
                    f['recorded_build_id'] is not None and image['build_id']!=f['recorded_build_id']):
                answer.update(attribution_state='Invalid',reason='Sampled ELF identity does not match available file')
            elif f['dso'] not in self.expected and f['recorded_build_id'] is None:answer['reason']='Neither a recorded build ID nor declared pre-run hash binds this ELF'
            elif f['file_offset'] is None:answer['reason']='No recorded DSO file offset'
            else:
                segments=[s for s in image['segments'] if s['offset']<=f['file_offset']<s['offset']+s['bytes']]
                if len(segments)!=1:answer['reason']='File offset has no unique ELF load-segment translation'
                else:
                    address=segments[0]['virtual']+f['file_offset']-segments[0]['offset']
                    queries.setdefault(f['dso'],{}).setdefault(address,[]).append((f,key));continue
            f.update(answer);self.cache[key]=answer
        for path,addresses in queries.items():
            items=list(addresses)
            for start in range(0,len(items),256):
                batch=items[start:start+256];self.serial+=1;folder=self.files[path]['folder']/('resolve-%06d'%self.serial)
                text=command([self.paths['addr2line'],'-a','-f','-C','-e',path,*map(hex,batch)],folder)
                lines=text.splitlines()
                if len(lines)!=len(batch)*3:raise LabError('parse_failed','Unexpected addr2line response cardinality',{'path':path,'evidence':str(folder)})
                for i,address in enumerate(batch):
                    if int(lines[i*3],16)!=address:raise LabError('parse_failed','addr2line response address differs from query')
                    symbol,source=lines[i*3+1:i*3+3];resolved=known_symbol(symbol)
                    answer=dict(symbol=symbol if resolved else '[unknown]',source_location=source,resolved=resolved,
                        attribution_state='Estimated' if resolved else 'Missing',symbol_source='addr2line',elf_address=address,
                        symbol_evidence=str(folder.relative_to(self.directory.parent)/'stdout.txt'))
                    for f,key in addresses[address]:f.update(answer);self.cache[key]=answer
        while len(self.cache)>4096:self.cache.popitem(last=False)

    def finish(self):
        for path,image in self.files.items():
            if image['evidence_state']=='Measured' and sha256(path)!=image['sha256']:
                raise LabError('invalid_request','ELF changed during symbol attribution',{'path':path})
        return [{k:v for k,v in image.items() if k!='folder'} for image in self.files.values()]


def quality(samples,total,known,stack,lost):
    return dict(sample_count=samples,total_period=total,resolved_self_fraction=known/total if total else None,
        unresolved_self_fraction=(total-known)/total if total else None,resolved_two_frame_fraction=stack/total if total else None,lost_events=lost,
        denominator='Sum of recorded cpu-clock periods; a two-frame sample requires both first frames resolved',complete_callchain_proven=False)


def normalize_trace(directory,options,paths,expected=None):
    directory=Path(directory);metadata=dict(mapping_records=0,lost_events=0)
    context=json.loads((directory/'sampling-context.json').read_text())
    attributes=(directory/'sample-attributes/stdout.txt').read_text()
    if not re.search(r'use_clockid\s*:\s*1',attributes) or not re.search(r'clockid\s*:\s*1(?:\D|$)',attributes):
        raise LabError('parse_failed','Recorded perf attributes do not establish CLOCK_MONOTONIC')
    context['verified_attributes']='sample-attributes/stdout.txt'
    if context.get('monotonic_offset_ns')!=0:
        raise LabError('unavailable','Recorded sampling clock has no supported zero namespace offset')
    context['after_capture_monotonic_ns']=time.monotonic_ns()
    resolver=ElfResolver(directory/'symbolizer',paths,expected or {}) if options.get('symbolizer','perf')=='addr2line' else None
    provenance=helper_evidence(paths,directory/'sampling-helpers') if resolver else {}
    histogram=Counter();period=known=stack=count=0;events=set()
    records=iter(native_samples(directory/'script/stdout.txt',metadata));finished=False
    with (directory/'cpu-samples.jsonl').open('w') as output:
        while not finished:
            batch=[]
            for _ in range(256):
                try:batch.append(next(records))
                except StopIteration:finished=True;break
            if resolver:resolver.resolve([f for s in batch for f in s['frames']])
            for sample in batch:
                if not context['before_launch_monotonic_ns']<=sample['time_ns']<=context['after_capture_monotonic_ns']:
                    raise LabError('parse_failed','CPU sample timestamp lies outside the verified monotonic capture bounds')
                if not resolver:
                    for f in sample['frames']:f.update(symbol=f['native_symbol'],resolved=known_symbol(f['native_symbol']),symbol_source='perf',attribution_state='Measured' if known_symbol(f['native_symbol']) else 'Missing')
                leaf=sample['frames'][0];count+=1;period+=sample['period'];events.add(sample['event'])
                if leaf['resolved']:known+=sample['period']
                if len(sample['frames'])>=2 and all(f['resolved'] for f in sample['frames'][:2]):stack+=sample['period']
                histogram[(leaf['symbol'],leaf['dso'])]+=sample['period']
                output.write(json.dumps(sample,sort_keys=True,allow_nan=False)+'\n')
    if len(events)>1:raise LabError('parse_failed','Mixed CPU sample event semantics')
    images=resolver.finish() if resolver else []
    q=quality(count,period,known,stack,metadata['lost_events'])
    profile=dict(schema_version=1,evidence_state='Measured' if count else 'Missing',quality=q,clock=context,event=next(iter(events)) if events else None,
        samples_evidence='cpu-samples.jsonl',native_evidence='script/stdout.txt',images=images,helpers=provenance,mapping_records=metadata['mapping_records'],
        limitations=['Sample periods are on-CPU sampling evidence, not exact elapsed time, operation counts or waiting time.',
            'Clock/PID namespaces must match explicit phase clock metadata; a relative steady-clock origin is not inferred.',
            'addr2line attribution derives from measured addresses, recorded offsets and verified ELF identity; it does not reconstruct missing callers.',
            'Two resolved initial frames establish partial interpretability, not a complete or necessarily correct unwind.',
            'Inclusive symbol shares use observed callchains only; recursive frames are deduplicated per sample.'])
    if not count:profile['reason']='No CPU samples captured; no zero-time inference'
    write_json(directory/'sampling-profile.json',profile)
    return dict(sampling_profile=profile,hotspots=[dict(symbol=symbol,dso=dso,self_percent=100*weight/period,evidence='cpu-samples.jsonl',
        method='sample-period-weighted',command='recorded target') for (symbol,dso),weight in histogram.most_common()],
        sample_count=count,unknown_symbol_self_percent=100*(period-known)/period if period else None)


def evaluate_precheck(profile,policy,phase_clock=None):
    from .comparison import combine_gates
    q=profile['quality'];checks={}
    enough=q['sample_count']>=policy['min_samples'] and not q['lost_events']
    checks['samples']=dict(status='PASS' if enough else 'INCONCLUSIVE',reason='Declared sample minimum and no recorded loss required')
    for label,field,threshold in [('self_symbols','resolved_self_fraction','min_resolved_self_fraction'),('partial_stack','resolved_two_frame_fraction','min_resolved_stack_fraction')]:
        state='INCONCLUSIVE' if not enough else 'PASS' if q[field]>=policy[threshold] else 'FAIL'
        checks[label]=dict(status=state,observed=q[field],threshold=policy[threshold])
    if policy['require_phase_clock']:
        valid=phase_clock is not None and profile['clock'].get('monotonic_offset_ns')==0 and all(phase_clock[k]==profile['clock'][k] for k in ('name','boot_id','pid_namespace','time_namespace'))
        checks['phase_clock']=dict(status='PASS' if valid else 'UNSUPPORTED',reason='Explicit matching clock and namespace identities required')
    return dict(status=combine_gates([c['status'] for c in checks.values()]),checks=checks,quality=q,policy=policy,phase_clock=phase_clock,
        meaning='Interpretability of one preflight execution; not correctness, statistical speedup or proof of complete stacks')
