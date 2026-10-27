"""Paired Study design: ordered ordinary runs, cluster inference in Comparison."""
import copy
from pathlib import Path
import random
import statistics

from .budget import execution_budget
from .comparison import validated_samples, summarize_comparison, validate_policy, request_differences
from .environment import fingerprints, identity_snapshot
from .errors import LabError
from .lab import run
from .protocol import fields, require, positive, load_request
from .storage import write_json, utc


def load_paired(spec, base):
    allowed=('schema_version','analysis','name','baseline','candidate','blocks','order','seed','budget','metric',
             'metric_scope','policy','constraints','resource_conditions','allow_command_change','secondary_metrics')
    required=tuple(k for k in allowed if k not in ('metric_scope','allow_command_change','secondary_metrics'))
    fields(spec,allowed,required,'paired study')
    require(type(spec['schema_version']) is int and spec['schema_version']==1,'Unsupported paired study version')
    require(spec['analysis']=='paired','Invalid paired analysis')
    require(all(isinstance(spec[k],str) and bool(spec[k]) for k in ('name','baseline','candidate','metric','constraints')),'Paired study text required')
    require(spec['order'] in ('random_ab_ba','abba'),'Supported paired orders: random_ab_ba, abba')
    require(type(spec['seed']) is int,'Order seed must be an integer')
    positive(spec['blocks'],'blocks',integer=True)
    require(spec['blocks']>=2,'Paired study requires at least two blocks')
    positive(spec['budget'],'budget',integer=True)
    require(type(spec.get('allow_command_change',False)) is bool,'allow_command_change must be boolean')
    require('metric_scope' not in spec or isinstance(spec['metric_scope'],str) and bool(spec['metric_scope']),'Invalid metric_scope')
    spec['policy']=validate_policy(spec['policy'])
    conditions=spec['resource_conditions']
    concurrent_fields=('concurrent_runs','background_load','notes','synchronization_timeout_seconds','minimum_overlap_fraction','threads_per_variant')
    fields(conditions,concurrent_fields,('concurrent_runs','background_load','notes'),'resource conditions')
    require(type(conditions['concurrent_runs']) is int and conditions['concurrent_runs'] in (1,2),'concurrent_runs must be 1 or 2')
    require(conditions['background_load'] in ('uncontrolled','caller_controlled'),'Invalid background load declaration')
    require(isinstance(conditions['notes'],str),'Resource notes must be text')
    if conditions['concurrent_runs']==2:
        positive(conditions.get('synchronization_timeout_seconds'),'synchronization_timeout_seconds')
        fraction=conditions.get('minimum_overlap_fraction')
        require(type(fraction) in (int,float) and 0 < fraction <= 1,'minimum_overlap_fraction must be in (0,1]')
        threads=conditions.get('threads_per_variant')
        fields(threads,('baseline','candidate'),('baseline','candidate'),'threads_per_variant')
        for count in threads.values():positive(count,'threads_per_variant',integer=True)
    else:
        require(not any(k in conditions for k in concurrent_fields[3:]),'Concurrent synchronization fields require concurrent_runs=2')
    descriptors=[{'metric':spec['metric'],'metric_scope':spec.get('metric_scope'),'policy':spec['policy']}]
    require(isinstance(spec.get('secondary_metrics',[]),list),'secondary_metrics must be an array')
    for descriptor in spec.get('secondary_metrics',[]):
        fields(descriptor,('metric','metric_scope','policy'),('metric','policy'),'secondary metric')
        require(isinstance(descriptor['metric'],str) and bool(descriptor['metric']),'Secondary metric ID required')
        require('metric_scope' not in descriptor or isinstance(descriptor['metric_scope'],str) and bool(descriptor['metric_scope']),'Invalid secondary scope')
        descriptor['policy']=validate_policy(descriptor['policy']);descriptors.append(descriptor)
    require(len({(d['metric'],d.get('metric_scope')) for d in descriptors})==len(descriptors),'Duplicate metric/scope comparison')
    requests={}
    for variant in ('baseline','candidate'):
        spec[variant]=str((base/spec[variant]).resolve())
        request=load_request(spec[variant])
        requests[variant]=request
    validate_pair_conditions(spec,requests)
    return spec,requests


def validate_pair_conditions(spec,requests):
    for request in requests.values():
        require(request['measurement']['repetitions']==1,'Each paired member must request exactly one observation; repeated ABBA members are not independent blocks')
        require(request['measurement']['tool']!='auto','Paired studies require explicitly selected tools; plan each request first')
    if spec['resource_conditions']['concurrent_runs']==2:
        from .adapters import REGISTRY
        require(all(not REGISTRY[r['measurement']['tool']].batch for r in requests.values()),'Concurrent pairs require non-batch external tools')
        if any('execution_state' in request for request in requests.values()):
            raise LabError('unavailable','Concurrent state-action boundaries cannot guarantee the requested serial preparation semantics')
    differences=request_differences(requests['baseline'],requests['candidate'],spec.get('allow_command_change',False))
    if differences:raise LabError('incomparable','Declared paired conditions differ before execution',{'differences':differences})
    require(requests['baseline']['measurement']['tool']==requests['candidate']['measurement']['tool'],'Paired variants require the same explicit adapter')
    require(sorted(set(requests['baseline']['workload']['inputs']))==sorted(set(requests['candidate']['workload']['inputs'])),'Paired variants must declare the same input files')
    needed=spec['blocks']*(4 if spec['order']=='abba' else 2)
    require(needed<=spec['budget'],'Paired run matrix exceeds explicit run budget')


def schedule(spec):
    rng=random.Random(spec['seed'])
    return [{'block_id':'block-%04d'%i,'index':i,'order':'ABBA' if spec['order']=='abba' else rng.choice(('AB','BA')),
             'status':'pending','members':[]} for i in range(spec['blocks'])]


def paired_budget(spec, requests):
    per_variant=spec['blocks']*(2 if spec['order']=='abba' else 1)
    return {'schema_version':1,'analysis':'paired','execution_performed':False,'order_seed':spec['seed'],
            'scheduled_blocks':schedule(spec),'run_calls':per_variant*2,'run_budget':spec['budget'],
            'variants':{name:{'run_calls':per_variant,'each_run':execution_budget(request)} for name,request in requests.items()},
            'resource_conditions':spec['resource_conditions'],'resource_isolation_enforced':False,
            'estimated_runtime_seconds':None,
            'limitations':['Each member reuses the full run lifecycle, including oracle, preflight and configured warmups. No hidden reuse or retries.',
                           'Counts multiply by run_calls for each variant; adaptive/opaque work remains unknown.',
                           'Preparation is serial; measurements overlap within each wave, and waves/blocks remain serial.'
                           if spec['resource_conditions']['concurrent_runs']==2 else
                           'Serial order controls platform launches, not unrelated background processes.']}


def compare_blocks(blocks, metric_name, policy, metric_scope=None, allow_command_change=False):
    """Validate all raw members before reducing ABBA to one point per variant/block."""
    require(bool(blocks),'No blocks to compare')
    references={};ids=set();pairs=[];unit=None;execution_kind=None;shared_context=None
    for block in blocks:
        require(block['status']=='completed','Incomplete/failed blocks cannot be omitted or scored')
        require(block['order'] in ('AB','BA','ABBA'),'Unknown block order')
        require(len(block['members'])==len(block['order']),'Incomplete block membership')
        values={'baseline':[],'candidate':[]};sources={'baseline':[],'candidate':[]}
        for slot,(letter,member) in enumerate(zip(block['order'],block['members'])):
            variant='baseline' if letter=='A' else 'candidate'
            require(member['variant']==variant and member['slot']==slot,'Ledger order does not match the preregistered block')
            measured=member['run']
            context=measured.get('execution_context') or {'kind':'serial'}
            kind=context.get('kind')
            require(kind in ('serial','concurrent_ab'),'Unknown paired execution context')
            normalized={k:context.get(k) for k in ('kind','concurrent_runs','competition_pair_identity','resource_conditions')}
            if execution_kind is None:execution_kind=kind;shared_context=normalized
            require(kind==execution_kind and normalized==shared_context,'Serial and concurrent members or differing concurrent conditions cannot be mixed')
            require(measured['run_id'] not in ids,'A measured run cannot be reused as multiple paired observations')
            ids.add(measured['run_id'])
            require(len(measured['samples'])==1,'One native observation per paired member required')
            reference=references.setdefault(variant,measured)
            require(measured['identity']['artifacts']==reference['identity']['artifacts'],'Variant artifact fingerprints changed across blocks')
            validated_samples(reference,measured,metric_name,False,metric_scope=metric_scope)
            if len(references)==2:
                left,right=(measured,references['candidate']) if variant=='baseline' else (references['baseline'],measured)
                validated_samples(left,right,metric_name,allow_command_change,metric_scope=metric_scope)
            selected=validated_samples(measured,measured,metric_name,False,metric_scope=metric_scope)[0][0]
            unit=selected['unit']
            values[variant].append(selected['value'])
            sources[variant].append({'run_id':measured['run_id'],'sample_index':measured['samples'][0]['index'],
                                     'slot':slot,'value':selected['value'],'evidence':selected['evidence'],
                                     'input_evidence_state':'Measured'})
        pairs.append({'block_id':block['block_id'],'order':block['order'],'baseline':statistics.median(values['baseline']),
                      'candidate':statistics.median(values['candidate']),'members':sources,'evidence_state':'Estimated'})
    a,b=[references[v] for v in ('baseline','candidate')]
    result=summarize_comparison(a,b,metric_name,unit,[p['baseline'] for p in pairs],[p['candidate'] for p in pairs],
                                policy,allow_command_change,metric_scope=metric_scope,paired=True)
    result['block_pairs']=pairs
    for variant in ('baseline','candidate'):
        result[variant+'_run_ids']=[s['run_id'] for pair in pairs for s in pair['members'][variant]]
    result['baseline_id_role']='first source reference; all baseline_run_ids contribute'
    result['candidate_id_role']='first source reference; all candidate_run_ids contribute'
    result['estimator']='median(block baseline medians) / median(block candidate medians)'
    result['limitations'].extend(['ABBA contributes one block observation, not two independent pairs.',
                                  'Marginal CVs describe drift/noise; the paired stability gate uses block-ratio CV.',
                                  'No claim of independent blocks, removal of background interference or causality follows from pairing.'])
    from .validity import evaluate_paired_validity, apply_validity
    from .comparison import normalized_cost, decisions
    apply_validity(result,evaluate_paired_validity(blocks,metric_name,metric_scope))
    result['normalized_cost']=normalized_cost(result)
    result['decisions']=decisions(result)
    if execution_kind=='concurrent_ab':
        result['execution_context']={'kind':'concurrent_ab','concurrent_runs':2,
                                     'competition_pair_identity':shared_context['competition_pair_identity'],
                                     'resource_conditions':shared_context['resource_conditions'],
                                     'scope':'competing pair'}
        result['limitations'].append('Concurrent A/B results describe this competing pair and do not estimate isolated serial speedup.')
    else:
        result['execution_context']={'kind':'serial','scope':'serial paired study'}
    return result


def run_paired(spec, requests, store):
    identifier,directory=store.create('studies')
    result={'schema_version':1,'study_id':identifier,'status':'running','started_at':utc(),'spec':spec,
            'runs':[],'comparisons':[],'growth':{},'secondary_growth':{},'blocks':schedule(spec),
            'execution_budget':paired_budget(spec,requests),'resource_isolation_enforced':False,'error':None}
    from .lifecycle import StudyProgress,notify_record_created
    from .workflowphase import begin, session, finalize_into
    progress=StudyProgress(result,directory,requests.values(),result['execution_budget']['run_calls'])
    write_json(directory/'study.json',result)
    completed=[]
    try:
        notify_record_created('study',identifier)
        record = begin(spec, requests, store, identifier, directory)
        if record is not None:
            result["workflow"] = record
            write_json(directory / "study.json", result)
        with session(record):
            expected={}
            for variant,request in requests.items():
                path=directory/variant;path.mkdir()
                expected[variant]=fingerprints(request,path)
            for block in result['blocks']:
                block['status']='running';native=[]
                if spec['resource_conditions']['concurrent_runs']==2:
                    from .concurrent import run_wave
                    wave_orders=[block['order'][:2],block['order'][2:]] if block['order']=='ABBA' else [block['order']]
                    for wave_index,wave_order in enumerate(wave_orders):
                        wave_id=block['block_id']+'-wave-%02d'%wave_index
                        members=[]
                        for slot_offset,letter in enumerate(wave_order):
                            slot=slot_offset if block['order']!='ABBA' else wave_index*2+slot_offset
                            variant='baseline' if letter=='A' else 'candidate'
                            request=copy.deepcopy(requests[variant])
                            request['name']=spec['name']+'/'+block['block_id']+'/'+str(slot)+'/'+variant
                            require(identity_snapshot(request)==expected[variant],'Inputs or artifacts changed before paired member launch')
                            members.append({'slot':slot,'variant':variant,'request':request})
                        wave_context={'kind':'concurrent_ab','study_id':identifier,'block_id':block['block_id'],'wave_id':wave_id,
                            'concurrent_runs':2,'competition_pair_identity':expected,
                            'resource_conditions':spec['resource_conditions']}
                        created=[]
                        def child_created(slot,variant,run_id):
                            child={'block_id':block['block_id'],'slot':slot,'variant':variant,'run_id':run_id,
                                   'status':'running','wave_id':wave_id}
                            result['runs'].append(child);block['members'].append(child)
                            index=progress.register_child('run',run_id,{'block_id':block['block_id'],'wave_id':wave_id,'slot':slot,'variant':variant})
                            created.append((child,index))
                            write_json(directory/'study.json',result)
                        try:
                            measured_runs,wave=run_wave(members,store,directory/'waves'/wave_id,wave_context,child_created)
                        except LabError as error:
                            wave=error.details.get('wave',{})
                            for child,index in created:
                                measured=store.read(child['run_id'])
                                child.update(status=measured['status'],started_at=measured['started_at'],ended_at=measured['ended_at'],wave_id=wave_id,wave=wave)
                                if index is not None:progress.finish_child(index)
                            write_json(directory/'study.json',result)
                            raise
                        by_id={record['run_id']:record for record in measured_runs}
                        for child,index in created:
                            measured=by_id[child['run_id']]
                            child.update(status=measured['status'],started_at=measured['started_at'],ended_at=measured['ended_at'],wave_id=wave_id,wave=wave)
                            if index is not None:progress.finish_child(index)
                        for item in members:
                            slot=item['slot'];variant=item['variant'];measured=by_id[next(c['run_id'] for c,_ in created if c['slot']==slot)]
                            if measured['status']!='completed':
                                block['status']='cancelled' if measured['status']=='cancelled' else 'failed'
                                raise LabError(measured['error']['category'],
                                    'Concurrent paired member failed; no block was dropped or retried',{'member':next(c for c,_ in created if c['slot']==slot),'wave':wave})
                            require(measured['identity']==expected[variant],'Inputs or artifacts changed since paired study registration')
                            native.append({**next(c for c,_ in created if c['slot']==slot),'run':measured})
                        write_json(directory/'study.json',result)
                    block['status']='completed';completed.append({**block,'members':native})
                    write_json(directory/'study.json',result)
                    continue
                for slot,letter in enumerate(block['order']):
                    variant='baseline' if letter=='A' else 'candidate'
                    result['active_member']={'block_id':block['block_id'],'slot':slot,'variant':variant}
                    write_json(directory/'study.json',result)
                    request=copy.deepcopy(requests[variant])
                    request['name']=spec['name']+'/'+block['block_id']+'/'+str(slot)+'/'+variant
                    require(identity_snapshot(request)==expected[variant],'Inputs or artifacts changed before paired member launch')
                    with progress.delegate('run',dict(result['active_member'])):
                        measured=run(request,store)
                    member={'block_id':block['block_id'],'slot':slot,'variant':variant,'run_id':measured['run_id'],
                            'status':measured['status'],'started_at':measured['started_at'],'ended_at':measured['ended_at']}
                    result['runs'].append(member);block['members'].append(member)
                    write_json(directory/'study.json',result)
                    if measured['status']!='completed':
                        block['status']='cancelled' if measured['status']=='cancelled' else 'failed'
                        details={'member':member,'error':measured['error']}
                        if 'acceptance' in measured['error']['details']:details['acceptance']=measured['error']['details']['acceptance']
                        raise LabError(measured['error']['category'],'Paired member failed; no block was dropped or retried',details)
                    require(measured['identity']==expected[variant],'Inputs or artifacts changed since paired study registration')
                    native.append({**member,'run':measured})
                block['status']='completed';completed.append({**block,'members':native})
                write_json(directory/'study.json',result)
            descriptors=[{'metric':spec['metric'],'metric_scope':spec.get('metric_scope'),'policy':spec['policy']},*spec.get('secondary_metrics',[])]
            # Validate every comparison before publishing any successful score.
            comparisons=[compare_blocks(completed,d['metric'],d['policy'],d.get('metric_scope'),spec.get('allow_command_change',False)) for d in descriptors]
            for comparison in comparisons:
                cid,folder=store.create('comparisons')
                comparison.update(comparison_id=cid,study_id=identifier)
                write_json(folder/'comparison.json',comparison)
                result['comparisons'].append(comparison)
            result['status']='completed'
    except LabError as error:
        result['status']='cancelled' if error.category=='cancelled' else 'failed';result['error']=error.as_dict()
    except KeyboardInterrupt:
        result['status']='cancelled';result['error']=LabError('cancelled','Paired study interrupted; no automatic retry').as_dict()
    except OSError as error:
        result['status']='failed';result['error']=LabError('storage_failed',str(error)).as_dict()
    except Exception as error:
        result['status']='failed';result['error']=LabError('internal_error',str(error)).as_dict()
        raise
    finally:
        finalize_into(result, store)
        progress.terminal()
        if result['status'] in ('failed','cancelled'):
            for block in result['blocks']:
                if block['status']=='running':block['status']=result['status']
        result['active_member']=None;result['ended_at']=utc()
        write_json(directory/'study.json',result)
    return result
