"""Read archived phase evidence; never execute a target to obtain a timeline."""
from .errors import LabError
from .protocol import positive,require,sha256
from .phases import normalize
from .views import evidence_path,build_view,evidence_id


def query_timeline(store,identifier,stage,measurement,track=None):
    require(stage in ('measurement','preflight'),'Invalid timeline execution stage')
    if stage=='measurement':positive(measurement,'measurement',integer=True,zero=True)
    else:require(measurement is None,'Preflight has no formal measurement group')
    require(track is None or isinstance(track,str) and bool(track),'track must be nonempty')
    result=store.read(identifier)
    references={t['evidence'] for t in result.get('thread_timelines',[]) if t['sample']==measurement and t['execution_stage']==stage}
    if not references:
        references={p['evidence'] for p in result.get('phases',[]) if p['sample']==measurement and p.get('execution_stage','measurement')==stage}
    if not references and 'phase_report' in result['request']['measurement']:
        folder='preflight' if stage=='preflight' else 'measurement-%03d'%measurement
        reference=folder+'/'+result['request']['measurement']['phase_report']
        if any(e['path']==reference for e in result.get('evidence',[])):references.add(reference)
    if not references:raise LabError('unavailable','No phase source was archived for this timeline launch group',{'evidence_state':'Missing'})
    require(len(references)==1,'One archived phase source is required for this timeline launch group')
    reference=next(iter(references));path,entry=evidence_path(store,identifier,reference)
    if sha256(path)!=entry['sha256']:raise LabError('storage_failed','Timeline phase source differs from its evidence inventory')
    timeline=normalize(path)['thread_timeline'];timeline['evidence']=reference
    if track is not None:
        timeline['tracks']=[t for t in timeline['tracks'] if t['track_id']==track]
        require(bool(timeline['tracks']),'Requested producer track does not exist in this phase source')
    measured=any(t['work_seconds'] is not None or t['wait_seconds'] is not None for t in timeline['tracks'])
    timeline['evidence_state']='Measured' if measured else 'Missing'
    return dict(timeline,run_id=identifier,run_status=result['status'],source_error=result['error'],error=result['error'],
        execution_stage=stage,measurement_group=measurement,selection_status='PASS' if measured else 'INCONCLUSIVE')


def timeline_view(store,selection):
    view=build_view(store,selection['run_id'])
    view['thread_timelines']=[dict(selection,evidence_id=evidence_id(selection['run_id'],selection['evidence']))]
    view['conditions']['timeline_selection']=dict(execution_stage=selection['execution_stage'],measurement_group=selection['measurement_group'])
    return view


def scheduler_timeline_view(store, selection):
    view=build_view(store,selection['run_id'])
    view['scheduler_timelines']=[]
    reference=selection.get('references',{}).get('profile')
    view['scheduler_queries']=[dict(selection,evidence_id=evidence_id(selection['run_id'],reference) if reference else None)]
    view['conditions']['scheduler_timeline_selection']=dict(execution_stage=selection['execution_stage'],
        measurement_group=selection['measurement_group'],selection=selection['selection'])
    return view
