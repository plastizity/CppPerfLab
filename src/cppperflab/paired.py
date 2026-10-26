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
    fields(conditions,('concurrent_runs','background_load','notes'),('concurrent_runs','background_load','notes'),'resource conditions')
    require(type(conditions['concurrent_runs']) is int and conditions['concurrent_runs']==1,'This design supports serial paired runs only; concurrent competition is a different experiment')
    require(conditions['background_load'] in ('uncontrolled','caller_controlled'),'Invalid background load declaration')
    require(isinstance(conditions['notes'],str),'Resource notes must be text')
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
                           'Serial order controls platform launches, not unrelated background processes.']}


def compare_blocks(blocks, metric_name, policy, metric_scope=None, allow_command_change=False):
    """Validate all raw members before reducing ABBA to one point per variant/block."""
    require(bool(blocks),'No blocks to compare')
    references={};ids=set();pairs=[];unit=None
    for block in blocks:
        require(block['status']=='completed','Incomplete/failed blocks cannot be omitted or scored')
        require(block['order'] in ('AB','BA','ABBA'),'Unknown block order')
        require(len(block['members'])==len(block['order']),'Incomplete block membership')
        values={'baseline':[],'candidate':[]};sources={'baseline':[],'candidate':[]}
        for slot,(letter,member) in enumerate(zip(block['order'],block['members'])):
            variant='baseline' if letter=='A' else 'candidate'
            require(member['variant']==variant and member['slot']==slot,'Ledger order does not match the preregistered block')
            measured=member['run']
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
    return result


def run_paired(spec, requests, store):
    identifier,directory=store.create('studies')
    result={'schema_version':1,'study_id':identifier,'status':'running','started_at':utc(),'spec':spec,
            'runs':[],'comparisons':[],'growth':{},'secondary_growth':{},'blocks':schedule(spec),
            'execution_budget':paired_budget(spec,requests),'resource_isolation_enforced':False,'error':None}
    from .lifecycle import StudyProgress,notify_record_created
    progress=StudyProgress(result,directory,requests.values(),result['execution_budget']['run_calls'])
    write_json(directory/'study.json',result)
    completed=[]
    try:
        notify_record_created('study',identifier)
        expected={}
        for variant,request in requests.items():
            path=directory/variant;path.mkdir()
            expected[variant]=fingerprints(request,path)
        for block in result['blocks']:
            block['status']='running';native=[]
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
        progress.terminal()
        if result['status'] in ('failed','cancelled'):
            for block in result['blocks']:
                if block['status']=='running':block['status']=result['status']
        result['active_member']=None;result['ended_at']=utc()
        write_json(directory/'study.json',result)
    return result
