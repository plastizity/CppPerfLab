"""Opaque, versioned sample bundles in the existing Store; no business decoder."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import random
import shutil

from .errors import LabError
from .metrics import evidence_state
from .protocol import fields, positive, require, sha256
from .storage import inventory, utc, write_json

FORMATS={'text':'.txt','json':'.json','binary':'.bin'}


def read_manifest(path):
    try:
        raw=Path(path).read_bytes()
        value=json.loads(raw,parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
        return value,raw
    except (OSError,ValueError) as error:
        raise LabError('invalid_request','Cannot read sample manifest',{'reason':str(error)}) from error


def relative_name(name):
    require(isinstance(name,str) and bool(name) and '\\' not in name and '\x00' not in name,'Invalid relative attachment name')
    path=PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts and str(path)==name and name!='.','Attachment must be a normalized relative path')
    return path


def payload_plan(value,base):
    fields(value,('format','format_version','path','value','attachments'),('format','format_version'),'payload')
    require(isinstance(value['format'],str) and value['format'] in FORMATS,'Unsupported payload format')
    require(isinstance(value['format_version'],str) and bool(value['format_version']),'Explicit payload format_version required')
    require(('path' in value)!=('value' in value),'Payload requires exactly one of path/value')
    data=None;source=None
    if 'path' in value:
        require(isinstance(value['path'],str) and bool(value['path']) and '\x00' not in value['path'],'Payload path required')
        source=(base/value['path']).resolve();require(source.is_file(),'Payload file missing: '+str(source))
        size=source.stat().st_size
    else:
        if value['format']=='text':
            require(isinstance(value['value'],str),'Inline text must be a string')
            try:data=value['value'].encode('utf-8')
            except UnicodeError as error:raise LabError('invalid_request','Inline text is not valid UTF-8',{'reason':str(error)}) from error
        else:
            require(value['format']=='json','Inline binary is unsupported; supply a file')
            try:data=json.dumps(value['value'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
            except (ValueError,TypeError,UnicodeError) as error:raise LabError('invalid_request','Invalid inline JSON',{'reason':str(error)}) from error
        size=len(data)
    entry='payload'+FORMATS[value['format']]
    files=[{'name':entry,'source':source,'data':data,'bytes':size}]
    require(isinstance(value.get('attachments',[]),list),'attachments must be an array')
    names={entry}
    for item in value.get('attachments',[]):
        fields(item,('name','path'),('name','path'),'attachment')
        name=str(relative_name(item['name']))
        require(name not in names,'Duplicate attachment name');names.add(name)
        require(isinstance(item['path'],str) and bool(item['path']) and '\x00' not in item['path'],'Attachment source path required')
        source=(base/item['path']).resolve();require(source.is_file(),'Attachment file missing: '+str(source))
        files.append({'name':name,'source':source,'data':None,'bytes':source.stat().st_size})
    # A file cannot also be a parent directory of another captured file.
    require(all(not any(other.startswith(name+'/') for other in names) for name in names),'Overlapping attachment paths')
    return {'format':value['format'],'format_version':value['format_version'],'entry':entry,'files':files,'bytes':sum(f['bytes'] for f in files)}


def source_observation(source,sample,store):
    from .views import evidence_path
    from .phases import normalize
    fields(source,('run_id','sample_index','phase_id','execution_stage'),('run_id','sample_index','phase_id'),'sample source')
    require(isinstance(source['run_id'],str) and isinstance(source['phase_id'],str),'Invalid sample source identity')
    stage=source.get('execution_stage','measurement')
    require(stage in ('preflight','measurement'),'Invalid source execution stage')
    if stage=='measurement':positive(source['sample_index'],'sample_index',zero=True,integer=True)
    else:require(source['sample_index'] is None,'Preflight diagnostics have no measurement sample index')
    run=store.read(source['run_id'])
    matches=[p for p in run.get('phases',[]) if p['sample']==source['sample_index'] and p['id']==source['phase_id'] and p.get('execution_stage','measurement')==stage]
    require(len(matches)==1,'Source phase must identify exactly one event')
    path,entry=evidence_path(store,source['run_id'],matches[0]['evidence'])
    if sha256(path)!=entry['sha256']:raise LabError('storage_failed','Source phase evidence hash mismatch')
    events=[p for p in normalize(path)['phases'] if p['id']==source['phase_id']]
    require(len(events)==1,'Source phase missing from raw evidence')
    event=events[0]
    require(event['input_id']==sample.get('input_id',sample['id']) and event['result']==sample['result'] and event['correctness']==sample['correctness'],
            'Sample identity/classification/correctness differs from source phase')
    return {'duration_seconds':event['inclusive_seconds'],'evidence_state':'Measured',
            'source':{**source,'execution_stage':stage,'scope':event['scope'],'evidence':entry['path'],'sha256':entry['sha256']}}


def observation(value):
    fields(value,('duration_seconds','evidence_state','reason'),('duration_seconds','evidence_state'),'sample observation')
    require('reason' not in value or isinstance(value['reason'],str) and bool(value['reason']),'Observation reason must be text')
    checked={'name':'sample.duration','value':value['duration_seconds'],**{k:v for k,v in value.items() if k!='duration_seconds'}}
    state=evidence_state(checked)
    if state in ('Measured','Estimated','Modeled'):positive(value['duration_seconds'],'duration_seconds',zero=True)
    return dict(value)


def load_capture(path,store):
    path=Path(path).resolve();spec,raw=read_manifest(path)
    fields(spec,('schema_version','name','samples','selection','max_payload_bytes'),('schema_version','name','samples','selection','max_payload_bytes'),'sample manifest')
    require(type(spec['schema_version']) is int and spec['schema_version']==1,'Unsupported sample manifest version')
    require(isinstance(spec['name'],str) and bool(spec['name']),'Sample collection name required')
    positive(spec['max_payload_bytes'],'max_payload_bytes',integer=True)
    require(isinstance(spec['samples'],list) and bool(spec['samples']),'Nonempty sample population required')
    samples=[];ids=set()
    for item in spec['samples']:
        fields(item,('id','input_id','input','expected','input_complete','result','correctness','tags','observation','source'),
               ('id','input','input_complete','result','correctness'),'sample')
        require(isinstance(item['id'],str) and bool(item['id']) and item['id'] not in ids,'Unique nonempty sample ID required');ids.add(item['id'])
        require('input_id' not in item or isinstance(item['input_id'],str) and bool(item['input_id']),'Producer input_id must be nonempty when supplied')
        require(item['input_complete'] is True,'Exporter must explicitly assert complete input; partial summaries cannot be replayed')
        require(isinstance(item['result'],str) and bool(item['result']),'Business result classification required')
        require(item['correctness'] in ('passed','failed','not_checked'),'Invalid sample correctness')
        tags=item.get('tags',[])
        require(isinstance(tags,list) and all(isinstance(t,str) and bool(t) for t in tags) and len(set(tags))==len(tags),'Invalid tags')
        require('source' in item or 'observation' in item,'Explicit source or observation state required; missing time is not zero')
        observed=source_observation(item['source'],item,store) if 'source' in item else observation(item['observation'])
        if 'source' in item and 'observation' in item:
            supplied=observation(item['observation'])
            require(all(supplied[k]==observed[k] for k in ('duration_seconds','evidence_state')),'Imported observation contradicts raw source')
        entry={'id':item['id'],'input_id':item.get('input_id',item['id']),'input_complete':True,'result':item['result'],'correctness':item['correctness'],
               'tags':tags,'observation':observed,'input':payload_plan(item['input'],path.parent)}
        if 'expected' in item:entry['expected']=payload_plan(item['expected'],path.parent)
        samples.append(entry)
    selection=spec['selection']
    fields(selection,('mode','seed','limit_per_result','results'),('mode',),'selection')
    require(selection['mode'] in ('all','longest_per_result','random_per_result'),'Unknown selection mode')
    require(isinstance(selection.get('results',[]),list) and all(isinstance(r,str) and bool(r) for r in selection.get('results',[])),'Invalid result filter')
    eligible=[s for s in samples if 'results' not in selection or s['result'] in selection['results']]
    if selection['mode']=='all':
        require(not set(selection)-{'mode','results'},'all selection has no random seed/limit')
        chosen=eligible
    else:
        require(type(selection.get('seed')) is int,'Selection seed required')
        positive(selection.get('limit_per_result'),'limit_per_result',integer=True)
        rng=random.Random(selection['seed']);chosen=[]
        for result in sorted({s['result'] for s in eligible}):
            group=sorted((s for s in eligible if s['result']==result),key=lambda s:s['id'])
            rng.shuffle(group)
            if selection['mode']=='longest_per_result':
                require(all(s['observation']['evidence_state']=='Measured' for s in group),'Longest selection requires Measured durations for every eligible input')
                group.sort(key=lambda s:s['observation']['duration_seconds'],reverse=True)
            chosen.extend(group[:selection['limit_per_result']])
    require(bool(chosen),'Selection captured no samples')
    chosen_ids={s['id'] for s in chosen}
    needed=sum(s[k]['bytes'] for s in samples if s['id'] in chosen_ids for k in ('input','expected') if k in s)
    require(needed<=spec['max_payload_bytes'],'Selected payload bytes exceed explicit storage budget')
    return spec,raw,samples,chosen_ids,needed


def capture_budget(path,store):
    spec,raw,samples,chosen,needed=load_capture(path,store)
    return {'schema_version':1,'execution_performed':False,'files_written':False,'file_format_validation':'during capture, on selected copies','population_count':len(samples),
            'selected_ids':sorted(chosen),'payload_bytes':needed,'max_payload_bytes':spec['max_payload_bytes'],
            'manifest_bytes':len(raw),'limitations':['Payload limit excludes collection metadata and original manifest bytes.',
                'Input completeness and opaque format semantics are exporter assertions, not inferred by the platform.']}


def capture(path,store):
    spec,raw,samples,chosen,needed=load_capture(path,store)
    identifier,directory=store.create('collections')
    record={'schema_version':1,'collection_id':identifier,'name':spec['name'],'status':'capturing','started_at':utc(),
            'selection':spec['selection'],'max_payload_bytes':spec['max_payload_bytes'],'payload_bytes':0,'samples':[],
            'source_manifest_sha256':hashlib.sha256(raw).hexdigest(),'selected_ids':sorted(chosen),'error':None,'evidence':[],
            'limitations':['Selection is for diagnosis, not a representative benchmark-suite score.',
                'Producer-only observations are reported evidence; source-linked observations were checked against raw phase evidence.',
                'Complete input and attachment semantics are declared by the exporter; opaque references are never rewritten.']}
    record['samples']=[{**{k:v for k,v in sample.items() if k not in ('input','expected')},'captured':False} for sample in samples]
    write_json(directory/'collection.json',record)
    try:
        (directory/'source-manifest.json').write_bytes(raw)
        for index,sample in enumerate(samples):
            entry=record['samples'][index]
            if sample['id'] not in chosen:continue
            for role in ('input','expected'):
                if role not in sample:continue
                payload=sample[role];target=directory/'payloads'/str(index)/role;target.mkdir(parents=True)
                files=[]
                for file in payload['files']:
                    dest=target/file['name'];dest.parent.mkdir(parents=True,exist_ok=True)
                    if file['source'] is not None:shutil.copyfile(file['source'],dest)
                    else:dest.write_bytes(file['data'])
                    size=dest.stat().st_size
                    require(size==file['bytes'],'Input file size changed during capture')
                    record['payload_bytes']+=size
                    require(record['payload_bytes']<=spec['max_payload_bytes'],'Captured data exceeded payload budget')
                    files.append({'path':str(dest.relative_to(directory)),'bytes':size,'sha256':sha256(dest)})
                try:
                    if payload['format']=='json':
                        with (target/payload['entry']).open(encoding='utf-8') as stream:
                            json.load(stream,parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                    elif payload['format']=='text':
                        with (target/payload['entry']).open(encoding='utf-8') as stream:
                            while stream.read(1024*1024):pass
                except (UnicodeError,ValueError) as error:
                    raise LabError('invalid_request','Captured text/JSON payload has invalid encoding or syntax',{'reason':str(error)}) from error
                entry[role]={'format':payload['format'],'format_version':payload['format_version'],
                             'entry':str((target/payload['entry']).relative_to(directory)),'files':files}
            if 'source' in entry['observation']:
                from .views import evidence_path
                source=entry['observation']['source']
                original,_=evidence_path(store,source['run_id'],source['evidence'])
                dest=directory/'source-evidence'/(source['sha256']+'.phases.json')
                if not dest.exists():
                    dest.parent.mkdir(exist_ok=True);shutil.copyfile(original,dest)
                if sha256(dest)!=source['sha256']:raise LabError('storage_failed','Source evidence changed during capture')
                source['archive_evidence']=str(dest.relative_to(directory))
            entry['captured']=True
        record['status']='completed'
    except LabError as error:record['status']='failed';record['error']=error.as_dict()
    except OSError as error:record['status']='failed';record['error']=LabError('storage_failed',str(error)).as_dict()
    except KeyboardInterrupt:record['status']='cancelled';record['error']=LabError('cancelled','Sample capture interrupted').as_dict()
    finally:
        record['ended_at']=utc();record['evidence']=[e for e in inventory(directory) if e['path']!='collection.json']
        write_json(directory/'collection.json',record)
    return record


def read_collection(store,identifier):
    record=store.read(identifier,'collection')
    fields(record,('schema_version','collection_id','name','status','started_at','ended_at','selection','max_payload_bytes','payload_bytes',
                   'samples','selected_ids','source_manifest_sha256','error','limitations','evidence'),
           ('schema_version','collection_id','name','status','started_at','selection','max_payload_bytes','payload_bytes',
            'samples','selected_ids','source_manifest_sha256','error','limitations','evidence'),'collection')
    require(type(record['schema_version']) is int and record['schema_version']==1,'Unsupported collection version')
    require(isinstance(record['evidence'],list) and isinstance(record['limitations'],list) and isinstance(record['selection'],dict),'Invalid collection metadata')
    def file_metadata(file):
        fields(file,('path','bytes','sha256'),('path','bytes','sha256'),'captured file')
        relative_name(file['path']);positive(file['bytes'],'bytes',integer=True,zero=True)
        require(isinstance(file['sha256'],str) and len(file['sha256'])==64 and all(c in '0123456789abcdef' for c in file['sha256']),'Invalid captured digest')
    for file in record['evidence']:file_metadata(file)
    require(record['collection_id']==identifier and isinstance(record['samples'],list),'Invalid collection identity/population')
    require(record['status'] in ('capturing','completed','failed','cancelled'),'Invalid capture status')
    ids=set()
    for sample in record['samples']:
        fields(sample,('id','input_id','input_complete','result','correctness','tags','observation','captured','input','expected'),
               ('id','input_complete','result','correctness','tags','observation','captured'),'captured sample')
        require(isinstance(sample['id'],str) and bool(sample['id']) and sample['id'] not in ids,'Invalid captured sample identity');ids.add(sample['id'])
        require('input_id' not in sample or isinstance(sample['input_id'],str) and bool(sample['input_id']),'Invalid producer input ID')
        require(type(sample['captured']) is bool and sample['input_complete'] is True,'Invalid capture completeness')
        require(isinstance(sample['result'],str) and bool(sample['result']) and sample['correctness'] in ('passed','failed','not_checked'),'Invalid captured result')
        require(isinstance(sample['tags'],list) and all(isinstance(t,str) for t in sample['tags']),'Invalid captured tags')
        value=sample['observation']
        fields(value,('duration_seconds','evidence_state','reason','source'),('duration_seconds','evidence_state'),'captured observation')
        observation({k:v for k,v in value.items() if k!='source'})
        if 'source' in value:
            source=value['source']
            fields(source,('run_id','sample_index','phase_id','execution_stage','scope','evidence','sha256','archive_evidence'),
                   ('run_id','sample_index','phase_id','scope','evidence','sha256'),'captured source')
            require(not sample['captured'] or 'archive_evidence' in source,'Captured source has no archived evidence')
            if 'archive_evidence' in source:relative_name(source['archive_evidence'])
        require(not sample['captured'] or 'input' in sample,'Captured sample has no input bundle')
        for role in ('input','expected'):
            if role not in sample:continue
            payload=sample[role]
            fields(payload,('format','format_version','entry','files'),('format','format_version','entry','files'),'captured payload')
            require(isinstance(payload['format'],str) and payload['format'] in FORMATS and isinstance(payload['format_version'],str) and bool(payload['format_version']),'Invalid captured format')
            require(isinstance(payload['files'],list) and bool(payload['files']),'Captured file inventory missing')
            relative_name(payload['entry'])
            for file in payload['files']:file_metadata(file)
            paths=[f['path'] for f in payload['files']]
            require(payload['entry'] in paths and len(set(paths))==len(paths),'Invalid payload file inventory')
    return record


def verify_sample(store,identifier,key):
    record=read_collection(store,identifier)
    require(record['status']=='completed','Only complete captures can be replayed')
    entries=[s for s in record['samples'] if s['id']==key]
    require(len(entries)==1 and entries[0]['captured'],'Sample was not captured; no fallback to original files')
    sample=entries[0];root=store.record_path(identifier,'collection').parent
    source_manifest=root/'source-manifest.json'
    if not source_manifest.is_file() or sha256(source_manifest)!=record['source_manifest_sha256']:
        raise LabError('storage_failed','Captured source manifest missing or changed')
    if 'source' in sample['observation']:
        source=sample['observation']['source'];relative_name(source['archive_evidence'])
        path=(root/source['archive_evidence']).resolve()
        require(path.is_relative_to(root.resolve()),'Source evidence escapes collection')
        if not path.is_file() or sha256(path)!=source['sha256']:raise LabError('storage_failed','Captured phase source evidence missing or changed')
    for role in ('input','expected'):
        if role not in sample:continue
        payload=sample[role]
        for file in payload['files']:
            path=(root/file['path']).resolve()
            require(path.is_relative_to(root.resolve()),'Captured path escapes collection')
            if not path.is_file() or path.stat().st_size!=file['bytes'] or sha256(path)!=file['sha256']:
                raise LabError('storage_failed','Captured payload missing or changed',{'path':file['path']})
            evidence=[e for e in record['evidence'] if e['path']==file['path']]
            require(len(evidence)==1 and evidence[0]['sha256']==file['sha256'],'Payload and evidence inventories disagree')
    return record,sample


def query_samples(store,identifier,result=None,correctness=None,sort='id',offset=0,limit=20):
    require(type(offset) is int and offset>=0 and type(limit) is int and 1<=limit<=200,'Invalid sample pagination')
    require(sort in ('id','elapsed_desc'),'Invalid sample ordering')
    record=read_collection(store,identifier)
    rows=[s for s in record['samples'] if (result is None or s['result']==result) and (correctness is None or s['correctness']==correctness)]
    rows.sort(key=(lambda s:s['id']) if sort=='id' else (lambda s:(s['observation']['duration_seconds'] is None,-s['observation']['duration_seconds'] if s['observation']['duration_seconds'] is not None else 0,s['id'])))
    return {'schema_version':1,'collection_id':identifier,'status':record['status'],'error':record['error'],'data':rows[offset:offset+limit],
            'pagination':{'offset':offset,'limit':limit,'total':len(rows)},'selection':record['selection'],
            'limitations':record['limitations']+['Captured means stored at import time; replay verifies current file integrity. Unknown durations sort last, never as measured zero.']}
