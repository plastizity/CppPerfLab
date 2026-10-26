"""Read-only period-weighted CPU queries over archived streaming sample evidence."""
from bisect import bisect_right
from collections import defaultdict
import hashlib
import json

from .errors import LabError
from .protocol import positive, require
from .views import evidence_path


def merge(intervals):
    rows=[]
    for lo,hi in sorted(intervals):
        if hi==lo:continue
        if rows and lo<=rows[-1][1]:rows[-1]=(rows[-1][0],max(hi,rows[-1][1]))
        else:rows.append((lo,hi))
    return rows


def self_intervals(event,children):
    lo,hi=event['absolute_start_ns'],event['absolute_end_ns'];result=[]
    for start,end in merge((c['absolute_start_ns'],c['absolute_end_ns']) for c in children):
        if lo<start:result.append((lo,start))
        lo=max(lo,end)
    if lo<hi:result.append((lo,hi))
    return result


def read_sample(raw):
    try:
        try:value=json.loads(raw)
        except ValueError as error:raise LabError('parse_failed','Cannot read archived CPU sample JSONL') from error
        require(isinstance(value,dict) and all(k in value for k in ('index','pid','tid','time_ns','period','event','evidence_state','frames')),'Incomplete CPU sample record')
        for key in ('index','time_ns'):positive(value[key],key,integer=True,zero=True)
        for key in ('pid','tid','period'):positive(value[key],key,integer=True)
        require(value['evidence_state']=='Measured' and isinstance(value['event'],str),'Invalid CPU sample provenance')
        require(isinstance(value['frames'],list) and bool(value['frames']),'CPU sample has no frames')
        for frame in value['frames']:
            require(isinstance(frame,dict) and all(k in frame for k in ('symbol','dso','resolved')),'Incomplete CPU frame')
            require(isinstance(frame['symbol'],str) and isinstance(frame['dso'],str) and type(frame['resolved']) is bool,'Invalid CPU frame fields')
        return value
    except LabError as error:
        if error.category!='invalid_request':raise
        raise LabError('parse_failed',str(error)) from error


def query_profile(store,id,stage,measurement,*,phase_id=None,phase_path=None,call_id=None,input_id=None,phase_mode='inclusive',pid=None,tid=None,limit=20):
    require(stage in ('preflight','measurement'),'Invalid sampling execution stage')
    if stage=='measurement':positive(measurement,'measurement',integer=True,zero=True)
    else:require(measurement is None,'Preflight has no formal measurement group')
    require(phase_mode in ('inclusive','self'),'Invalid phase sampling mode')
    for value,name in ((pid,'pid'),(tid,'tid')):
        if value is not None:positive(value,name,integer=True)
    positive(limit,'limit',integer=True)
    for value,name in ((phase_id,'phase_id'),(call_id,'call_id')):
        require(value is None or isinstance(value,str) and bool(value),name+' must be nonempty')
    require(input_id is None or isinstance(input_id,str),'input_id must be a string; empty means unknown')
    if phase_path is not None:require(isinstance(phase_path,list) and bool(phase_path) and all(isinstance(s,str) and bool(s) for s in phase_path),'phase_path must be a nonempty JSON string array')
    selected_phase=any(v is not None for v in (phase_id,phase_path,call_id,input_id))
    require(phase_mode=='inclusive' or selected_phase,'Self phase mode requires explicit phase selection')
    run=store.read(id);profiles=run.get('sampling_profiles',[])
    result=dict(schema_version=1,run_id=id,status=run['status'],error=run['error'],execution_stage=stage,measurement_group=measurement,
        selection=dict(phase_id=phase_id,phase_path=phase_path,call_id=call_id,input_id=input_id,phase_mode=phase_mode,pid=pid,tid=tid),
        selection_status='UNSUPPORTED',evidence_state='Unsupported',hotspots=[],denominator=None,selected_phase_ids=[],quality=None,
        limitations=['Percentages use recorded CPU event periods, not exact wall time, call counts or a causal optimization cost.',
                     'Self symbol share uses the sampled leaf; inclusive symbol share uses observed frames once per symbol/DSO per sample.',
                     'Phase selection is restricted to the explicitly recorded OS threads; uninstrumented worker threads are not assigned to the model.',
                     'Zero selected samples do not imply zero CPU work. Snapshot timestamps do not prove complete or correct callchains.'])
    def unsupported(reason):
        result.update(reason=reason,error=LabError('unavailable',reason).as_dict(),source_error=run['error'])
        return result
    if not profiles:return unsupported('No structured CPU trace was collected; legacy aggregate percentages cannot be phase-filtered')
    matches=[p for p in profiles if p['execution_stage']==stage and p['measurement_group']==measurement]
    require(len(matches)==1,'Sampling launch group missing or ambiguous')
    profile=matches[0];result['quality']=profile['quality'];result['clock']=profile['clock']
    regions=None
    if selected_phase:
        phases=[p for p in run.get('phases',[]) if p.get('execution_stage','measurement')==stage and p['sample']==measurement]
        clocks=[c['clock'] for c in run.get('phase_clocks',[]) if c['execution_stage']==stage and c['sample']==measurement]
        if profile['clock'].get('monotonic_offset_ns')!=0 or len(clocks)!=1 or not all(clocks[0][k]==profile['clock'][k] for k in ('name','boot_id','pid_namespace','time_namespace')):
            return unsupported('Phase and CPU sample clocks/namespace identities are not explicitly correlated')
        selected=[p for p in phases if (phase_id is None or p['id']==phase_id) and (phase_path is None or p['path']==phase_path) and
            (call_id is None or p['call_id']==call_id) and (input_id is None or p['input_id']==input_id)]
        require(bool(selected),'No phase event matches all requested filters')
        if phase_mode=='self' and any(not p['children_complete'] for p in selected):return unsupported('Self phase selection requires complete declared immediate-child coverage')
        regions=defaultdict(list)
        for p in selected:
            intervals=[(p['absolute_start_ns'],p['absolute_end_ns'])] if phase_mode=='inclusive' else self_intervals(p,[c for c in phases if c['parent_id']==p['id']])
            regions[(p['pid'],p['tid'])].extend(intervals)
        regions={key:merge(value) for key,value in regions.items()};starts={key:[lo for lo,_ in value] for key,value in regions.items()}
        result['selected_phase_ids']=[p['id'] for p in selected]
    path,entry=evidence_path(store,id,profile['samples_evidence'])
    digest=hashlib.sha256();total=selected_period=count=selected_count=unknown=0;rows={}
    with path.open('rb') as stream:
        for raw in stream:
            digest.update(raw)
            s=read_sample(raw)
            if s['index']!=count or s['event']!=profile['event']:
                raise LabError('parse_failed','CPU sample index/event semantics differ from profile')
            total+=s['period'];count+=1
            if pid is not None and s['pid']!=pid or tid is not None and s['tid']!=tid:continue
            if regions is not None:
                key=(s['pid'],s['tid']);index=bisect_right(starts.get(key,[]),s['time_ns'])-1
                if index<0 or s['time_ns']>=regions[key][index][1]:continue
            selected_period+=s['period'];selected_count+=1
            leaf=s['frames'][0]
            if not leaf['resolved']:unknown+=s['period']
            unique={(f['symbol'],f['dso']) for f in s['frames']}
            for symbol,dso in unique:
                row=rows.setdefault((symbol,dso),dict(symbol=symbol,dso=dso,self_period=0,inclusive_period=0))
                row['inclusive_period']+=s['period']
            rows[(leaf['symbol'],leaf['dso'])]['self_period']+=s['period']
    if digest.hexdigest()!=entry['sha256']:raise LabError('storage_failed','CPU trace hash differs from archived evidence inventory')
    if count!=profile['quality']['sample_count'] or total!=profile['quality']['total_period']:
        raise LabError('parse_failed','CPU trace and archived profile counters differ')
    ranked=sorted(rows.values(),key=lambda r:(-r['self_period'],-r['inclusive_period'],r['symbol'],r['dso']))
    for r in ranked:
        r.update(self_percent=100*r['self_period']/selected_period,inclusive_percent=100*r['inclusive_period']/selected_period,
                 evidence=profile['samples_evidence'],method='recorded-period weighted; selected CPU samples',sample=measurement,command='selected recorded target')
    result.update(selection_status='PASS' if selected_count else 'INCONCLUSIVE',evidence_state='Measured' if selected_count else 'Missing',
        hotspots=ranked[:limit],hotspot_count=len(ranked),samples_evidence=profile['samples_evidence'],
        denominator=dict(selected_sample_count=selected_count,whole_sample_count=count,selected_recorded_period=selected_period,whole_recorded_period=total,
            selected_fraction_of_recorded_period=selected_period/total if total else None,
            unresolved_selected_self_fraction=unknown/selected_period if selected_period else None,
            basis='Period sum of the selected cpu-clock samples from exactly one launch group',unit='event period'))
    if not selected_count:result['reason']='No recorded CPU samples in the selected window; no zero-execution-time inference'
    return result


def profile_view(store,selection):
    from .views import base_view,evidence_id
    from .performance_summary import summarize_performance
    run=store.read(selection['run_id'])
    view=base_view('run',selection['run_id'],selection['status'],selection['error'])
    view['overview']=dict(name=run['request']['name']+' / selected CPU samples',workload=run['request']['workload'])
    view['conditions']=dict(request=run['request'],selection=selection['selection'],sampling_quality=selection['quality'])
    view['cpu_selection']=selection;view['hotspots']=[dict(row,evidence_id=evidence_id(run['run_id'],row['evidence'])) for row in selection['hotspots']];view['summary']=summarize_performance([])
    view['evidence']=[dict(e,id=evidence_id(run['run_id'],e['path']),run_id=run['run_id']) for e in run['evidence']]
    view['limitations']=selection['limitations']
    return view
