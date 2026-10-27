"""Run progress and current process observations; never performance measurements."""
import os
from pathlib import Path
import statistics
import hashlib
from importlib.resources import files
from contextlib import contextmanager
from contextvars import ContextVar

from .errors import LabError
from .protocol import sha256
from .storage import utc, write_json


_record_created=ContextVar('cppperflab_record_created',default=None)


def implementation_identity():
    """Fingerprint the installed orchestration/parser implementation and schemas."""
    digest=hashlib.sha256()
    def visit(directory,prefix=''):
        for entry in sorted(directory.iterdir(),key=lambda p:p.name):
            if entry.name=='__pycache__':continue
            name=prefix+entry.name
            if entry.is_dir():visit(entry,name+'/')
            elif entry.name.endswith(('.py','.json')):
                digest.update(name.encode('utf-8')+b'\0')
                digest.update(hashlib.sha256(entry.read_bytes()).digest())
    visit(files('cppperflab'))
    return digest.hexdigest()


def start_workflow_cost(started_ns,with_progress):
    clock=dict(name='CLOCK_MONOTONIC',boot_id=None,time_namespace=None)
    try:
        clock.update(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                     time_namespace=os.readlink('/proc/self/ns/time'))
    except (FileNotFoundError,PermissionError) as error:clock['context_reason']=str(error)
    cost=dict(schema_version=1,method='monotonic-run-lifetime',
        scope='CppPerfLab run() entry through final cleanup/inventory, before final result write; not target performance time',
        started_monotonic_ns=started_ns,clock=clock,cost_key=None,
        implementation_sha256=implementation_identity(),elapsed_seconds=None,evidence_state='Measured')
    if not with_progress:cost['owner']=process_identity(os.getpid())
    return cost


def notify_record_created(kind,identifier):
    observer=_record_created.get()
    if observer is not None:observer(kind,identifier)


class StudyProgress:
    """Link existing serial delegations before their child starts any target."""
    def __init__(self,result,directory,requests,planned_runs):
        self.result=result;self.path=Path(directory)/'study.json'
        self.enabled=any(r['measurement'].get('progress',False) for r in requests)
        if self.enabled:
            result['lifecycle']=dict(schema_version=1,owner=process_identity(os.getpid()),updated_at=utc(),
                planned_runs=planned_runs,children=[],active_children=[],active_child=None)

    def register_child(self,kind,identifier,context):
        """Persist a child edge and mark it active; called by the parent writer."""
        if not self.enabled:return None
        state=self.result['lifecycle']
        state['children'].append(dict(record_kind=kind,record_id=identifier,context=context,created_at=utc()))
        index=len(state['children'])-1
        state['active_children'].append(index)
        state['active_child']=state['active_children'][0] if len(state['active_children'])==1 else None
        self.save()
        return index

    def finish_child(self,index):
        """Clear a child from the active set while retaining its persisted edge."""
        if not self.enabled:return None
        state=self.result['lifecycle']
        state['active_children'].remove(index)
        state['active_child']=state['active_children'][0] if len(state['active_children'])==1 else None
        self.save()

    def save(self):
        self.result['lifecycle']['updated_at']=utc();write_json(self.path,self.result)

    @property
    def data(self):
        return self.result['lifecycle']

    @contextmanager
    def delegate(self,kind,context):
        created=False
        def register(actual,identifier):
            nonlocal created
            if created or actual!=kind:raise LabError('internal_error','Study delegation published an unexpected child record')
            created=True;index_holder.append(self.register_child(kind,identifier,context))
        index_holder=[]
        token=_record_created.set(register if self.enabled else None)
        try:
            yield
            if self.enabled and not created:raise LabError('internal_error','Study delegation did not publish its child record')
        finally:
            _record_created.reset(token)
            if self.enabled and index_holder:self.finish_child(index_holder[0])

    def terminal(self):
        if self.enabled:
            self.result['lifecycle']['active_children']=[]
            self.result['lifecycle']['active_child']=None;self.result['lifecycle']['updated_at']=utc()


def process_identity(pid):
    """A PID alone is not an identity, including for a persisted running record."""
    result=dict(pid=pid,evidence_state='Measured')
    try:
        root=Path('/proc')/str(pid)
        stat=(root/'stat').read_text()
        tail=stat[stat.rindex(')')+2:].split()
        result.update(start_ticks=int(tail[19]),process_state=tail[0],
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            pid_namespace=os.readlink(root/'ns/pid'))
    except (FileNotFoundError,ProcessLookupError):
        result.update(evidence_state='Missing',reason='Process exited before its identity could be read')
    except PermissionError as error:
        result.update(evidence_state='Unsupported',reason=str(error))
    return result


def observe_process(identity):
    if identity['evidence_state']!='Measured':
        return dict(alive=None,state='unverified',evidence_state=identity['evidence_state'],reason=identity['reason'])
    try:
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        namespace=os.readlink('/proc/self/ns/pid')
    except (FileNotFoundError,PermissionError) as error:
        return dict(alive=None,state='unverified',evidence_state='Unsupported',reason=str(error))
    if boot!=identity['boot_id'] or namespace!=identity['pid_namespace']:
        return dict(alive=None,state='different_context',evidence_state='Unsupported',reason='Boot/PID namespace differs from the recorded process identity')
    current=process_identity(identity['pid'])
    if current['evidence_state']=='Missing':
        return dict(alive=False,state='exited',evidence_state='Measured')
    if current['evidence_state']!='Measured':
        return dict(alive=None,state='unverified',evidence_state=current['evidence_state'],reason=current['reason'])
    if current['start_ticks']!=identity['start_ticks']:
        return dict(alive=False,state='pid_reused',evidence_state='Measured')
    alive=current['process_state'] not in ('Z','X','x')
    return dict(alive=alive,state='alive' if alive else 'exited',evidence_state='Measured',process_state=current['process_state'])


class RunProgress:
    """Single orchestrator writes the existing result at execution boundaries."""
    def __init__(self,result,directory):
        self.result=result;self.path=Path(directory)/'result.json'
        result['lifecycle']=dict(schema_version=1,owner=process_identity(os.getpid()),stage='initializing',
            updated_at=utc(),active_process=None,launches={},measurement_groups=dict(completed=0,planned=None,durations_seconds=[]),
            normalized_samples=0,eta=dict(evidence_state='Missing',total_remaining_seconds=None,
                reason='No measured basis for a total-run ETA'))

    @property
    def data(self):return self.result['lifecycle']

    def save(self,stage=None):
        if stage is not None:self.data['stage']=stage
        self.data['updated_at']=utc();self.data['normalized_samples']=len(self.result['samples'])
        write_json(self.path,self.result)

    def configure(self,budget):
        self.data['launches']={s['stage']:dict(planned=s['commands'],started=0,finished=0) for s in budget['stages']
            if s['stage'] in ('correctness','preflight','warmup','measurement','per_sample_validation')}
        self.data['measurement_groups']['planned']=self.data['launches']['measurement']['planned']
        self.save('fingerprinting')

    def starting(self,stage,group,argv,directory,pid):
        if stage in self.data['launches']:self.data['launches'][stage]['started']+=1
        self.data['active_process']=dict(identity=process_identity(pid),argv=argv,execution_stage=stage,
            measurement_group=group,evidence_directory=str(directory),started_at=utc(),
            meaning='Owned execution group leader; a profiler/launcher can wrap the target')
        self.save(stage)

    def finished(self,stage):
        if stage in self.data['launches']:self.data['launches'][stage]['finished']+=1
        self.data['active_process']=None;self.save(stage+'_processing')

    def group_finished(self,seconds):
        groups=self.data['measurement_groups'];groups['durations_seconds'].append(seconds);groups['completed']+=1
        values=groups['durations_seconds'];remaining=groups['planned']-groups['completed']
        if len(values)>=2:
            self.data['eta']=dict(evidence_state='Estimated',total_remaining_seconds=None,
                measurement_groups_remaining=remaining,measurement_stage_remaining_seconds=statistics.median(values)*remaining,
                observed_range_seconds=[min(values)*remaining,max(values)*remaining],source_groups=len(values),as_of=utc(),
                scope='Remaining full measurement pipelines at this group boundary; excludes final hashing/summarization; not a total-run ETA',
                limitations=['Descriptive estimate from this run; not a confidence interval or guarantee.',
                    'The running group is counted in full until the next boundary; no inferred intra-command progress.'])
        self.save('measurement_group_complete')

    def terminal(self):
        self.data['active_process']=None;self.data['stage']=self.result['status'];self.data['updated_at']=utc()
        self.data['normalized_samples']=len(self.result['samples'])
        self.data['eta']=dict(evidence_state='Missing',total_remaining_seconds=None,reason='Run is terminal; no future execution is scheduled')


def status_from_result(store,result,verify_artifacts=False):
    from .run_notes import notes
    lifecycle=result.get('lifecycle')
    identity=lifecycle['owner'] if lifecycle else result.get('workflow_cost',{}).get('owner')
    owner=observe_process(identity) if identity is not None else dict(alive=None,state='unverified',evidence_state='Missing',reason='Legacy run has no owner identity')
    state='terminal' if result['status']!='running' else 'running_confirmed' if owner['alive'] is True else 'owner_exited' if owner['alive'] is False else 'unverified'
    active=lifecycle['active_process'] if lifecycle else None
    artifacts=[]
    if verify_artifacts:
        for path,expected in result.get('identity',{}).get('artifacts',{}).items():
            row=dict(path=path,recorded_sha256=expected)
            try:
                actual=sha256(path);row.update(evidence_state='Measured' if actual==expected else 'Invalid',current_sha256=actual,matches=actual==expected)
            except FileNotFoundError:row.update(evidence_state='Missing',matches=None,reason='Declared artifact no longer exists')
            except PermissionError as error:row.update(evidence_state='Unsupported',matches=None,reason=str(error))
            artifacts.append(row)
    verified=bool(artifacts) and all(a['matches'] is True for a in artifacts)
    if not verify_artifacts:
        verification=dict(evidence_state='Missing',reason='Current artifact verification was not requested')
    elif not artifacts:
        verification=dict(evidence_state='Missing',reason='No recorded artifact fingerprints are available yet')
    else:
        artifact_state=next((s for s in ('Invalid','Missing','Unsupported') if any(a['evidence_state']==s for a in artifacts)),'Measured')
        verification=dict(evidence_state=artifact_state,reason='Explicit current-file checks against recorded run fingerprints; per-file results retained')
    from .workflow_eta import estimate_run_eta
    return dict(schema_version=1,run_id=result['run_id'],run_status=result['status'],source_error=result['error'],error=None,
        name=result['request']['name'],started_at=result['started_at'],ended_at=result.get('ended_at'),observed_at=utc(),observed_state=state,
        owner=owner,active_process=None if active is None else dict(active,observation=observe_process(active['identity'])),
        progress=lifecycle,workflow_eta=estimate_run_eta(store,result,owner),
        build=dict(declared=result['request']['target']['build'],recorded_identity=result.get('identity'),binary=result.get('binary'),
            runtime_identities=result.get('runtime_identities',[])),current_artifacts=artifacts,artifacts_verified=verified,artifact_verification=verification,
        candidate=notes(store,result['run_id']),
        limitations=['Liveness is a point-in-time process identity observation, not proof that a command is making progress.',
            'Started/finished launch counts are process groups, not completed function calls or equal units of work.',
            'Candidate disposition and supersession are operator metadata, not correctness or performance acceptance.'])


def run_status(store,identifier,verify_artifacts=False):
    return status_from_result(store,store.read(identifier),verify_artifacts)
