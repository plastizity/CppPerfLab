"""Preregistered multi-profile Study composition; no new statistics or executor."""
import copy
import json
import random

from .adapters import REGISTRY
from .cases import case_budget, run_cases
from .comparison import decisions, validate_policy, combine_gates
from .environment import identity_snapshot
from .errors import LabError
from .performance_summary import dimension
from .paired import paired_budget, run_paired, validate_pair_conditions
from .protocol import fields, load_request, positive, require
from .replay import bind_sample
from .storage import utc, write_json

ROLES=('end_to_end','stage','work','resource','numerical')
GATES=('informational','non_regression','performance_target','statistics','correctness')


def load_assessment(spec,base,store):
    names=('schema_version','analysis','name','collection_id','samples','requests','profiles','seed','budget','constraints',
           'allow_command_change','resource_conditions')
    fields(spec,names,tuple(n for n in names if n!='allow_command_change'),'assessment study')
    require(type(spec['schema_version']) is int and spec['schema_version']==1 and spec['analysis']=='assessment','Unsupported assessment version/analysis')
    require(all(isinstance(spec[k],str) and bool(spec[k]) for k in ('name','collection_id','constraints')),'Assessment identity/constraints required')
    require(type(spec['seed']) is int and type(spec.get('allow_command_change',False)) is bool,'Invalid assessment seed/command treatment')
    require(isinstance(spec['samples'],list) and bool(spec['samples']) and all(isinstance(s,str) and bool(s) for s in spec['samples']) and len(set(spec['samples']))==len(spec['samples']),'Explicit distinct sample IDs required')
    fields(spec['requests'],('baseline','candidate'),('baseline','candidate'),'assessment requests')
    templates={}
    for variant,path in spec['requests'].items():
        require(isinstance(path,str) and bool(path) and '\x00' not in path,'Reviewed request path required')
        spec['requests'][variant]=str((base/path).resolve());templates[variant]=load_request(spec['requests'][variant])
    conditions=spec['resource_conditions']
    fields(conditions,('concurrent_runs','background_load','notes'),('concurrent_runs','background_load','notes'),'resource conditions')
    require(type(conditions['concurrent_runs']) is int and conditions['concurrent_runs']==1,'Assessment composes serial experiments only')
    require(conditions['background_load'] in ('uncontrolled','caller_controlled') and isinstance(conditions['notes'],str),'Invalid resource declaration')
    positive(spec['budget'],'budget',integer=True)
    require(isinstance(spec['profiles'],list) and bool(spec['profiles']),'Explicit profiling configurations required')
    ids=set();roles=[];needed=0
    for profile in spec['profiles']:
        fields(profile,('id','tool','options','warmups','design','comparisons','claim'),('id','tool','options','warmups','design','comparisons'),'assessment profile')
        if profile.get('claim') is not None:
            from .contracts import validate_claim
            validate_claim(profile['claim'])
        require(isinstance(profile['id'],str) and bool(profile['id']) and profile['id'] not in ids,'Distinct profile ID required');ids.add(profile['id'])
        require(isinstance(profile['tool'],str) and profile['tool'] in REGISTRY,'Explicit registered profile tool required')
        require(not REGISTRY[profile['tool']].batch,'Assessment profiles require individually scoped observations, not batch adapters')
        require(isinstance(profile['options'],dict),'Profile options must be an object');REGISTRY[profile['tool']].validate(profile['options'])
        positive(profile['warmups'],'warmups',integer=True,zero=True)
        design=profile['design']
        require(isinstance(design,dict) and design.get('kind') in ('paired','unpaired'),'Explicit paired/unpaired design required')
        if design['kind']=='paired':
            fields(design,('kind','blocks','order'),('kind','blocks','order'),'paired profile design')
            positive(design['blocks'],'blocks',integer=True)
            require(design['blocks']>=2 and design['order'] in ('random_ab_ba','abba'),'Invalid paired block design')
            count=design['blocks']*(4 if design['order']=='abba' else 2)
        else:
            fields(design,('kind','repetitions'),('kind','repetitions'),'unpaired profile design')
            positive(design['repetitions'],'repetitions',integer=True);count=2
        needed+=count*len(spec['samples'])
        require(isinstance(profile['comparisons'],list) and bool(profile['comparisons']),'Profile comparisons required')
        keys=set();names=set()
        for descriptor in profile['comparisons']:
            fields(descriptor,('id','role','metric','metric_scope','gate','policy'),('id','role','metric','gate','policy'),'assessment comparison')
            require(isinstance(descriptor['id'],str) and bool(descriptor['id']) and descriptor['id'] not in names,'Distinct comparison ID required');names.add(descriptor['id'])
            require(descriptor['role'] in ROLES and descriptor['gate'] in GATES,'Invalid comparison role/gate')
            require(isinstance(descriptor['metric'],str) and bool(descriptor['metric']),'Metric ID required')
            require('metric_scope' not in descriptor or isinstance(descriptor['metric_scope'],str) and bool(descriptor['metric_scope']),'Invalid exact metric scope')
            expected_dimension={'end_to_end':'timing','stage':'timing','work':'work','resource':'resources','numerical':'correctness'}[descriptor['role']]
            require(dimension(descriptor['metric'])==expected_dimension,'Metric meaning does not match the declared assessment role')
            descriptor['policy']=validate_policy(descriptor['policy'])
            require(descriptor['gate']!='performance_target' or 'min_speedup' in descriptor['policy'],'Performance target gate requires a declared target')
            if descriptor['role']=='end_to_end':
                require(descriptor['policy'].get('direction','lower')=='lower','End-to-end elapsed time has a lower-is-better objective')
                require(descriptor['metric']=='time.wall' and design['kind']=='paired' and descriptor['gate'] in ('non_regression','performance_target'),
                        'End-to-end outcome requires paired process time with an explicit performance gate')
            key=(descriptor['metric'],descriptor.get('metric_scope'))
            require(key not in keys,'Duplicate metric/scope in profile');keys.add(key);roles.append(descriptor['role'])
    require(roles.count('end_to_end')==1,'Exactly one end-to-end outcome required for every case')
    require('work' in roles,'A work comparison is required; incomplete work coverage must remain explicit')
    require(needed<=spec['budget'],'Full assessment exceeds declared run budget')
    jobs=[]
    for sample in spec['samples']:
        for profile in spec['profiles']:
            requests={};modified={};design=profile['design']
            for variant,template in templates.items():
                request=copy.deepcopy(template)
                request['measurement'].update(tool=profile['tool'],options=copy.deepcopy(profile['options']),warmups=profile['warmups'],
                    repetitions=1 if design['kind']=='paired' else design['repetitions'])
                if 'claim' in profile:
                    if profile['claim'] is None:request.pop('claim',None)
                    else:request['claim']=copy.deepcopy(profile['claim'])
                modified[variant]=request
                requests[variant]=bind_sample(store,spec['collection_id'],sample,request)
            descriptors=profile['comparisons']
            common=dict(schema_version=1,name=spec['name']+'/'+sample+'/'+profile['id'],seed=spec['seed'],constraints=spec['constraints'],
                        allow_command_change=spec.get('allow_command_change',False))
            if design['kind']=='paired':
                child=dict(common,analysis='paired',baseline=spec['requests']['baseline'],candidate=spec['requests']['candidate'],blocks=design['blocks'],order=design['order'],
                    budget=design['blocks']*(4 if design['order']=='abba' else 2),resource_conditions=conditions,
                    **{k:descriptors[0][k] for k in ('metric','metric_scope','policy') if k in descriptors[0]},
                    secondary_metrics=[{k:d[k] for k in ('metric','metric_scope','policy') if k in d} for d in descriptors[1:]])
                validate_pair_conditions(child,requests)
            else:
                from .comparison import request_differences
                differences=request_differences(requests['baseline'],requests['candidate'],common['allow_command_change'])
                if differences:raise LabError('incomparable','Assessment variants differ before execution',{'differences':differences})
                child=dict(schema_version=1,analysis='cases',name=common['name'],seed=spec['seed'],constraints=spec['constraints'],
                    collection_id=spec['collection_id'],samples=[sample],requests=dict(spec['requests']),budget=2,
                    comparisons=[dict(baseline='baseline',candidate='candidate',allow_command_change=common['allow_command_change'],
                        **{k:d[k] for k in ('metric','metric_scope','policy') if k in d}) for d in descriptors])
            jobs.append(dict(sample_id=sample,profile_id=profile['id'],kind=design['kind'],spec=child,requests=requests,templates=modified,descriptors=descriptors))
    return spec,jobs


def assessment_budget(spec,jobs):
    rows=[]
    for job in jobs:
        budget=paired_budget(job['spec'],job['requests']) if job['kind']=='paired' else case_budget(job['spec'],{(job['sample_id'],v):r for v,r in job['requests'].items()})
        rows.append(dict(sample_id=job['sample_id'],profile_id=job['profile_id'],budget=budget))
    return dict(schema_version=1,analysis='assessment',execution_performed=False,run_calls=sum(r['budget']['run_calls'] for r in rows),
        run_budget=spec['budget'],jobs=rows,estimated_runtime_seconds=None,
        limitations=['All substudies use existing run lifecycles; no probe, oracle, build or target executes in this preview.',
                     'Profiles share pinned case/variant identities; different tools retain independent scopes and samples.',
                     'Metadata hashing is preparation I/O and can warm caches; unrelated background load remains uncontrolled.'])


def assess_rows(spec,rows):
    cases=[]
    for sample in spec['samples']:
        selected=[r for r in rows if r['case_id']==sample]
        planned=sum(len(p['comparisons']) for p in spec['profiles'])
        require(len(selected)==planned,'Assessment comparison matrix incomplete')
        gates=[];correctness=[];work=[];primary=[]
        for row in selected:
            decision=decisions(row)
            correctness.append(decision['correctness']['status'])
            if decision['validity']['status']!='NOT_CONFIGURED':
                gates.append(decision['validity']['status'])
            if row['assessment_gate']!='informational':gates.append(decision[row['assessment_gate']]['status'])
            if row['assessment_role']=='end_to_end':primary.append(decision['improvement']['status'])
            if row['assessment_role']=='work':
                work.append(dict(comparison_id=row['comparison_id'],metric=row['metric'],scope=row.get('metric_scope'),
                                 baseline=row['baseline_median'],candidate=row['candidate_median'],change_fraction=row['change_fraction'],unit=row['unit']))
        acceptance=combine_gates(gates+correctness)
        cases.append(dict(case_id=sample,correctness=combine_gates(correctness),acceptance=acceptance,
            end_to_end_improvement=primary[0],gates=[dict(comparison_id=r['comparison_id'],profile_id=r['profile_id'],
                role=r['assessment_role'],gate=r['assessment_gate'],decisions=decisions(r)) for r in selected],work=work,
            work_attribution='recorded_work_differs; no single-step optimization inference' if any(w['baseline']!=w['candidate'] for w in work) else
                             'recorded_work_unchanged; unrecorded work is not proven constant'))
    return dict(schema_version=1,correctness=combine_gates([c['correctness'] for c in cases]),
        acceptance=combine_gates([c['acceptance'] for c in cases]),
        end_to_end_improvement='SUPPORTED_IMPROVEMENT' if all(c['end_to_end_improvement']=='SUPPORTED_IMPROVEMENT' for c in cases) else
                              'SUPPORTED_REGRESSION' if any(c['end_to_end_improvement']=='SUPPORTED_REGRESSION' for c in cases) else 'NOT_ESTABLISHED',
        cases=cases,optimization_accepted=False,
        limitations=['PASS means all declared gates passed; it does not by itself establish acceleration or authorize deployment.',
                     'Every selected case remains mandatory; no weighted score hides a failed or inconclusive case.',
                     'Cross-profile association checks pinned program/input identity; it never pools instrumented and native timings.',
                     'Work counters cover only their declared semantics. Changed work prevents automatic single-step attribution.'])


def run_assessment(spec,jobs,store):
    id,directory=store.create('studies')
    result=dict(schema_version=1,study_id=id,spec=spec,status='running',started_at=utc(),error=None,
        runs=[],comparisons=[],growth={},secondary_growth={},jobs=[],identities=[],execution_budget=assessment_budget(spec,jobs),
        assessment=dict(schema_version=1,correctness='INCONCLUSIVE',acceptance='INCONCLUSIVE',end_to_end_improvement='NOT_ESTABLISHED',cases=[],optimization_accepted=False))
    from .lifecycle import StudyProgress,notify_record_created
    from .workflowphase import begin, session, finalize_into
    progress=StudyProgress(result,directory,(r for job in jobs for r in job['requests'].values()),result['execution_budget']['run_calls'])
    order=list(range(len(jobs)));random.Random(spec['seed']).shuffle(order)
    result['jobs']=[dict(index=i,sample_id=jobs[i]['sample_id'],profile_id=jobs[i]['profile_id'],status='pending',study_id=None) for i in order]
    write_json(directory/'study.json',result)
    try:
        notify_record_created('study',id)
        record = begin(spec, {json.dumps([job["sample_id"], job["profile_id"], variant]): request for job in jobs for variant, request in job["requests"].items()}, store, id, directory)
        if record is not None:
            result["workflow"] = record
            write_json(directory / "study.json", result)
        with session(record):
            expected={}
            for job in jobs:
                for variant,request in job['requests'].items():
                    key=(job['sample_id'],variant);identity=identity_snapshot(request)
                    require(expected.setdefault(key,identity)==identity,'Profile changed case/variant identity before execution')
            result['identities']=[dict(case_id=s,variant=v,identity=i) for (s,v),i in expected.items()]
            for row in result['jobs']:
                job=jobs[row['index']];sample=job['sample_id'];row['status']='running';result['active_job']=row['index']
                write_json(directory/'study.json',result)
                for variant,request in job['requests'].items():require(identity_snapshot(request)==expected[(sample,variant)],'Assessment inputs/artifacts changed before profile launch')
                folder=directory/('job-%03d'%row['index']);folder.mkdir()
                child_spec=copy.deepcopy(job['spec'])
                for variant in ('baseline','candidate'):
                    path=folder/(variant+'.request.json')
                    write_json(path,job['requests'][variant] if job['kind']=='paired' else job['templates'][variant])
                    if job['kind']=='paired':child_spec[variant]=str(path)
                    else:child_spec['requests'][variant]=str(path)
                write_json(folder/'study.json',child_spec)
                with progress.delegate('study',dict(index=row['index'],sample_id=sample,profile_id=job['profile_id'])):
                    child=run_paired(child_spec,job['requests'],store) if job['kind']=='paired' else run_cases(child_spec,{(sample,v):r for v,r in job['requests'].items()},store)
                row.update(status=child['status'],study_id=child['study_id'])
                result['runs'].extend({**r,'case_id':sample,'profile_id':job['profile_id'],'source_study_id':child['study_id']} for r in child['runs'])
                write_json(directory/'study.json',result)
                if child['status']!='completed':
                    result['assessment']['acceptance']=child['error']['details'].get('acceptance',{}).get('status','INCONCLUSIVE')
                    raise LabError(child['error']['category'],'Assessment profile failed; no partial assessment is accepted',{'study_id':child['study_id'],'error':child['error']})
                for member in child['runs']:
                    native=store.read(member['run_id'])
                    require(native['identity']==expected[(sample,member['variant'])],'Measured profile identity differs from assessment registration')
                for variant,request in job['requests'].items():require(identity_snapshot(request)==expected[(sample,variant)],'Assessment inputs/artifacts changed after profile execution')
                for descriptor,c in zip(job['descriptors'],child['comparisons']):
                    require((c['metric'],c.get('metric_scope'))==(descriptor['metric'],descriptor.get('metric_scope')),'Child comparison order/meaning changed')
                    result['comparisons'].append(dict(c,case_id=sample,profile_id=job['profile_id'],assessment_role=descriptor['role'],
                        assessment_gate=descriptor['gate'],assessment_comparison_id=descriptor['id'],source_study_id=child['study_id']))
                require(len(child['comparisons'])==len(job['descriptors']),'Child comparison matrix incomplete')
            result['assessment']=assess_rows(spec,result['comparisons']);result['status']='completed'
    except LabError as error:
        result['status']='cancelled' if error.category=='cancelled' else 'failed';result['error']=error.as_dict()
        if error.category in ('unavailable','permission_denied'):result['assessment']['acceptance']='UNSUPPORTED'
        elif error.category=='correctness_failed':
            result['assessment']['acceptance']='FAIL';result['assessment']['correctness']='FAIL'
    except KeyboardInterrupt:result['status']='cancelled';result['error']=LabError('cancelled','Assessment cancelled').as_dict()
    except OSError as error:result['status']='failed';result['error']=LabError('storage_failed',str(error)).as_dict()
    except Exception as error:
        result['status']='failed';result['error']=LabError('internal_error',str(error)).as_dict();raise
    finally:
        finalize_into(result, store)
        progress.terminal()
        for row in result['jobs']:
            if row['status']=='running':row['status']=result['status']
        result['active_job']=None;result['ended_at']=utc();write_json(directory/'study.json',result)
    return result
