"""Opt-in Linux process/map snapshots; file hashes are not memory-page hashes."""
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from .errors import LabError
from .protocol import fields, positive, require
from .storage import utc, write_json


def validate_config(config,base=None):
    fields(config,('stages','interval_ms','max_snapshots','max_processes','required_objects'),
           ('stages','interval_ms','max_snapshots','max_processes'),'runtime identity')
    require(isinstance(config['stages'],list) and bool(config['stages']) and
            all(s in ('preflight','measurement') for s in config['stages']) and len(set(config['stages']))==len(config['stages']),
            'Runtime identity stages must be distinct preflight/measurement entries')
    for key in ('interval_ms','max_snapshots','max_processes'):positive(config[key],key,integer=True)
    require(isinstance(config.get('required_objects',[]),list),'required_objects must be an array')
    selectors=set()
    for rule in config.get('required_objects',[]):
        fields(rule,('basename','path','sha256'),('basename','path','sha256'),'runtime object expectation')
        require(isinstance(rule['basename'],str) and rule['basename'] not in ('','.','..') and
                Path(rule['basename']).name==rule['basename'] and '\x00' not in rule['basename'] and rule['basename'] not in selectors,
                'Distinct object basenames required');selectors.add(rule['basename'])
        require(isinstance(rule['path'],str) and bool(rule['path']) and '\x00' not in rule['path'],'Expected object path required')
        require(isinstance(rule['sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',rule['sha256']),'Expected SHA-256 required')
        if base is not None:rule['path']=str((base/rule['path']).resolve())
        else:require(Path(rule['path']).is_absolute(),'Expected object path must be resolved')
        try:rule['path'].encode('utf-8');rule['basename'].encode('utf-8')
        except UnicodeError as error:raise LabError('invalid_request','Runtime identity paths must be UTF-8') from error


def process_stat(text):
    """comm may contain spaces and parentheses; starttime anchors the PID lifetime."""
    try:
        end=text.rindex(')');pid=int(text[:text.index(' ')])
        tail=text[end+2:].split()
        return dict(pid=pid,state=tail[0],ppid=int(tail[1]),pgrp=int(tail[2]),session=int(tail[3]),start_ticks=int(tail[19]))
    except (ValueError,IndexError) as error:raise LabError('parse_failed','Malformed /proc stat') from error


def executable_maps(text):
    result=[]
    try:
        for line in text.splitlines():
            parts=line.split(None,5)
            if len(parts)<5:raise ValueError('missing map fields')
            start,end=(int(v,16) for v in parts[0].split('-'))
            major,minor=(int(v,16) for v in parts[3].split(':'))
            inode=int(parts[4]);offset=int(parts[2],16)
            if end<=start or len(parts[1])!=4:raise ValueError('invalid map interval/permissions')
            if 'x' not in parts[1] or inode==0 or len(parts)==5 or not parts[5].startswith('/'):continue
            path=parts[5]
            result.append(dict(path=path,device=[major,minor],inode=inode,start=start,end=end,offset=offset,permissions=parts[1],
                ambiguous_path=path.endswith(' (deleted)') or '\\012' in path))
    except ValueError as error:raise LabError('parse_failed','Malformed /proc maps',{'reason':str(error)}) from error
    return result


def signature(s):
    return dict(device=[os.major(s.st_dev),os.minor(s.st_dev)],inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)


def _read_at(fd,name):
    with os.fdopen(os.open(name,os.O_RDONLY,dir_fd=fd),'rb') as stream:return stream.read()


def _encoded(data):return base64.b64encode(data).decode('ascii')


class RuntimeObserver:
    def __init__(self,directory,config,target,direct=False):
        self.directory=Path(directory);self.config=config;self.target=signature(os.stat(target))
        self.target_path=str(target);self.direct=direct;self.images={};self.handles={};self.objects={};self.index=[];self.errors=[];self.truncations=set()
        self.started_ns=time.monotonic_ns();self.overhead_ns=0;self.target_snapshots=0;self.root_pid=None
        self.raw=self.directory/'runtime-snapshots';self.raw.mkdir()

    @property
    def active(self):return len(self.index)<self.config['max_snapshots']

    def _admit(self,pid):
        if pid in self.handles:return
        if len(self.handles)>=self.config['max_processes']:
            self.truncations.add('max_processes');return
        try:self.handles[pid]=os.open('/proc/'+str(pid),os.O_RDONLY|os.O_DIRECTORY)
        except (FileNotFoundError,ProcessLookupError):self.errors.append(dict(pid=pid,evidence_state='Missing',reason='Process directory unavailable during snapshot'))
        except PermissionError as error:self.errors.append(dict(pid=pid,evidence_state='Unsupported',reason=str(error)))

    def _object(self,row,pid,start_ticks,evidence,source="maps"):
        key=(pid,start_ticks,row['path'],tuple(row['device']),row['inode'])
        if key in self.objects:
            self.objects[key]['last_snapshot']=evidence
            if source not in self.objects[key]['sources']:self.objects[key]['sources'].append(source)
            return
        obj={k:row[k] for k in ('path','device','inode','ambiguous_path')}
        obj.update(pid=pid,start_ticks=start_ticks,evidence_state='Measured',first_snapshot=evidence,last_snapshot=evidence,
                   sources=[source],file_signature=None,hash=dict(evidence_state='Missing',sha256=None,reason='Hash not collected yet'))
        if row['ambiguous_path']:obj['hash']['reason']='Deleted or ambiguous escaped pathname; no substitute file is hashed'
        else:
            try:
                s=os.stat(row['path']);current=signature(s)
                if not stat.S_ISREG(s.st_mode):obj['hash'].update(evidence_state='Unsupported',reason='Mapping path is not a regular file')
                elif any(current[k]!=row[k] for k in ('device','inode')):obj['hash'].update(evidence_state='Invalid',reason='Path identity differs from mapped device/inode')
                else:obj['file_signature']=current
            except (FileNotFoundError,ProcessLookupError) as error:obj['hash']['reason']=str(error)
            except PermissionError as error:obj['hash'].update(evidence_state='Unsupported',reason=str(error))
        self.objects[key]=obj

    def sample(self,root_pid):
        started=time.monotonic_ns()
        if not self.active:
            self.truncations.add('max_snapshots');return
        self.root_pid=root_pid;self._admit(root_pid)
        rows=[];queue=list(self.handles);seen=set();evidence='runtime-snapshots/%06d.json'%len(self.index)
        try:
            while queue:
                pid=queue.pop(0)
                if pid in seen:continue
                seen.add(pid);fd=self.handles[pid];row=dict(pid=pid,raw={},evidence_state='Missing')
                rows.append(row)
                try:
                    data=_read_at(fd,'stat');row['raw']['stat_b64']=_encoded(data)
                    info=process_stat(data.decode('utf-8'));row['process']=info
                    if info['session']!=root_pid:
                        row['reason']='Process is outside the owned execution session';continue
                    data=_read_at(fd,'maps');row['raw']['maps_b64']=_encoded(data)
                    maps=executable_maps(data.decode('utf-8'))
                    data=os.readlink(b'exe',dir_fd=fd);row['raw']['exe_b64']=_encoded(data)
                    row['actual_executable']=data.decode('utf-8');row['executable_signature']=signature(os.stat('exe',dir_fd=fd))
                    row['raw']['cmdline_b64']=_encoded(_read_at(fd,'cmdline'))
                    row['target_match']='direct_launch' if self.direct and pid==root_pid else 'executable' if all(row['executable_signature'][k]==self.target[k] for k in ('device','inode')) else (
                        'mapped_image' if any(all(m[k]==self.target[k] for k in ('device','inode')) for m in maps) else None)
                    row['executable_mappings']=maps;row['evidence_state']='Measured'
                    image_key=(pid,info['start_ticks'],row['actual_executable'],*row['executable_signature']['device'],row['executable_signature']['inode'])
                    image=self.images.setdefault(image_key,dict(pid=pid,start_ticks=info['start_ticks'],actual_executable=row['actual_executable'],
                        executable_signature=row['executable_signature'],target_match=row['target_match'],evidence_state='Measured',first_snapshot=evidence))
                    image['last_snapshot']=evidence
                    if row['target_match']:
                        self.target_snapshots+=1
                        self._object(dict(path=row['actual_executable'],device=row['executable_signature']['device'],inode=row['executable_signature']['inode'],
                            ambiguous_path=row['actual_executable'].endswith(' (deleted)') or '\\012' in row['actual_executable']),pid,info['start_ticks'],evidence,'proc_exe')
                        for m in maps:self._object(m,pid,info['start_ticks'],evidence)
                    # Children can be created by any thread. Reads are observations, not a complete fork trace.
                    task_fd=os.open('task',os.O_RDONLY|os.O_DIRECTORY,dir_fd=fd)
                    try:
                        row['raw']['children']={}
                        for tid in sorted(os.listdir(task_fd),key=int):
                            try:raw=_read_at(task_fd,tid+'/children')
                            except (FileNotFoundError,ProcessLookupError):continue
                            row['raw']['children'][tid]=_encoded(raw)
                            for child in map(int,raw.split()):
                                self._admit(child)
                                if child in self.handles and child not in seen:queue.append(child)
                    finally:os.close(task_fd)
                except (FileNotFoundError,ProcessLookupError) as error:
                    row.update(evidence_state='Missing',reason=str(error));self.errors.append(dict(pid=pid,evidence_state='Missing',reason=str(error),snapshot=evidence))
                except (PermissionError,UnicodeError) as error:
                    row.update(evidence_state='Unsupported',reason=str(error));self.errors.append(dict(pid=pid,evidence_state='Unsupported',reason=str(error),snapshot=evidence))
        finally:
            ended=time.monotonic_ns()
            write_json(self.directory/evidence,dict(schema_version=1,started_monotonic_ns=started,ended_monotonic_ns=ended,processes=rows))
            self.index.append(dict(path=evidence,started_monotonic_ns=started,ended_monotonic_ns=ended,
                processes=len(rows),target_processes=sum(bool(r.get('target_match')) for r in rows)))
            self.overhead_ns+=time.monotonic_ns()-started

    def finish(self):
        hashing_started=time.monotonic_ns()
        for fd in self.handles.values():os.close(fd)
        cache={}
        for obj in self.objects.values():
            expected=obj['file_signature']
            if expected is None:continue
            key=(obj['path'],*expected['device'],expected['inode'],expected['size'],expected['mtime_ns'],expected['ctime_ns'])
            if key in cache:obj['hash']=cache[key];continue
            digest=dict(evidence_state='Missing',sha256=None,reason='File hash unavailable')
            try:
                with open(obj['path'],'rb') as stream:
                    before=signature(os.fstat(stream.fileno()))
                    if before!=expected:digest.update(evidence_state='Invalid',reason='File metadata changed after mapping observation')
                    else:
                        h=hashlib.sha256()
                        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
                        if signature(os.fstat(stream.fileno()))!=before:digest.update(evidence_state='Invalid',reason='File changed while hashing')
                        else:digest=dict(evidence_state='Measured',sha256=h.hexdigest(),collected_at=utc(),method='post-execution stable-file digest; not in-memory code bytes')
            except (FileNotFoundError,ProcessLookupError) as error:digest['reason']=str(error)
            except PermissionError as error:digest.update(evidence_state='Unsupported',reason=str(error))
            obj['hash']=digest;cache[key]=digest
        state='Measured' if self.target_snapshots else 'Unsupported' if any(e['evidence_state']=='Unsupported' for e in self.errors) else 'Missing'
        rules=[]
        for expected in self.config.get('required_objects',[]):
            matches=[o for o in self.objects.values() if Path(o['path']).name==expected['basename']]
            if not matches:verdict='UNSUPPORTED' if state=='Unsupported' else 'INCONCLUSIVE';reason='Required object was not observed in an identified target process'
            elif any(o['path']!=expected['path'] or (o['hash']['evidence_state']=='Measured' and o['hash']['sha256']!=expected['sha256']) for o in matches):
                verdict='FAIL';reason='Observed object path or SHA-256 differs from expectation'
            elif any(o['hash']['evidence_state']!='Measured' for o in matches):
                verdict='UNSUPPORTED' if any(o['hash']['evidence_state']=='Unsupported' for o in matches) else 'INCONCLUSIVE';reason='An observed object has no valid file hash'
            else:verdict='PASS';reason='Expected identity was observed; not proof of complete loader history'
            rules.append(dict(expected=expected,status=verdict,reason=reason,matches=[dict(pid=o['pid'],start_ticks=o['start_ticks'],path=o['path'],hash=o['hash']) for o in matches]))
        acceptance='FAIL' if any(r['status']=='FAIL' for r in rules) else 'INCONCLUSIVE' if any(r['status']=='INCONCLUSIVE' for r in rules) else 'UNSUPPORTED' if any(r['status']=='UNSUPPORTED' for r in rules) else 'PASS' if rules else 'NOT_REQUESTED'
        report=dict(schema_version=1,evidence_state=state,target_executable=self.target_path,target_signature=self.target,
            root_pid=self.root_pid,direct_target_launch=self.direct,config=self.config,snapshots=self.index,actual_executables=list(self.images.values()),objects=list(self.objects.values()),errors=self.errors,
            target_snapshots=self.target_snapshots,complete_loader_history=False,truncations=sorted(self.truncations),
            collection_elapsed_seconds=(hashing_started-self.started_ns)/1e9,observer_work_wall_seconds=self.overhead_ns/1e9,
            post_execution_hashing_wall_seconds=(time.monotonic_ns()-hashing_started)/1e9,
            acceptance=dict(status=acceptance,rules=rules,reason={'PASS':'Declared runtime identities were observed and matched',
                'FAIL':'An observed runtime identity conflicts with expectation','INCONCLUSIVE':'Required runtime identity evidence is incomplete',
                'UNSUPPORTED':'Runtime identity checking is unavailable under observed access/interpretation limits','NOT_REQUESTED':'No runtime identity obligations were declared'}[acceptance]),limitations=[
                'Periodic Linux /proc observations can miss short processes, dlopen/dlclose and exec transitions; children lists are not a complete fork trace.',
                'Executable file mappings are observed, not proof that every mapped function executed. Anonymous/JIT/vDSO mappings have no file digest.',
                'Objects belong to direct target launches or processes matched by actual executable/mapped target device/inode; wrapper executables remain separately identified.',
                'File SHA-256 is measured after execution with device/inode/metadata checks; this is not a hash of live memory or proof against runtime patching.',
                'Observation reads/writes compete with the target; preflight-only evidence does not observe subsequent measured launches.',
                'Process/session escape is unsupported. Snapshot/process limits and permission errors never imply complete coverage.'])
        if state!='Measured':report['reason']='No identified target process was captured; see snapshot timing, limits and permission/exit diagnostics'
        write_json(self.directory/'runtime-identity.json',report)
        return report
