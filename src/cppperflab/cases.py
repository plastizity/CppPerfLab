"""File-driven case matrices extend Study and delegate all measurement/comparison."""
import copy
from pathlib import Path
import random

from .budget import execution_budget
from .comparison import compare,validate_policy,request_differences
from .errors import LabError
from .lab import run
from .protocol import fields,require,positive,load_request
from .replay import bind_sample
from .samples import read_collection
from .storage import utc,write_json


def load_cases(spec,base,store):
    fields(spec,('schema_version','analysis','name','collection_id','samples','requests','comparisons','seed','budget','constraints'),
           ('schema_version','analysis','name','collection_id','samples','requests','comparisons','seed','budget','constraints'),'case study')
    require(type(spec['schema_version']) is int and spec['schema_version']==1 and spec['analysis']=='cases','Unsupported case study version/analysis')
    require(all(isinstance(spec[k],str) and bool(spec[k]) for k in ('name','collection_id','constraints')),'Case study identity/constraints required')
    require(type(spec['seed']) is int,'Case order seed required')
    require(isinstance(spec['samples'],list) and bool(spec['samples']) and all(isinstance(s,str) and bool(s) for s in spec['samples']) and len(set(spec['samples']))==len(spec['samples']),'Explicit distinct sample IDs required')
    require(isinstance(spec['requests'],dict) and bool(spec['requests']) and all(isinstance(k,str) and bool(k) and isinstance(v,str) and bool(v) for k,v in spec['requests'].items()),'Named reviewed request files required')
    positive(spec['budget'],'budget',integer=True)
    needed=len(spec['samples'])*len(spec['requests'])
    require(needed<=spec['budget'],'Case matrix exceeds explicit run budget')
    templates={}
    for variant,path in spec['requests'].items():
        spec['requests'][variant]=str((base/path).resolve());templates[variant]=load_request(spec['requests'][variant])
    require(isinstance(spec['comparisons'],list),'comparisons must be an explicit array; empty means profiling only')
    keys=set()
    for item in spec['comparisons']:
        fields(item,('baseline','candidate','metric','metric_scope','policy','allow_command_change'),('baseline','candidate','metric','policy'),'case comparison')
        require(isinstance(item['baseline'],str) and isinstance(item['candidate'],str),'Comparison variants must be strings')
        require(item['baseline'] in templates and item['candidate'] in templates and item['baseline']!=item['candidate'],'Comparison must name distinct configured variants')
        require(isinstance(item['metric'],str) and bool(item['metric']),'Comparison metric required')
        require('metric_scope' not in item or isinstance(item['metric_scope'],str) and bool(item['metric_scope']),'Invalid exact metric scope')
        require(type(item.get('allow_command_change',False)) is bool,'allow_command_change must be boolean')
        item['policy']=validate_policy(item['policy'])
        key=(item['baseline'],item['candidate'],item['metric'],item.get('metric_scope'))
        require(key not in keys,'Duplicate case comparison');keys.add(key)
    bound={}
    for sample in spec['samples']:
        for variant,template in templates.items():bound[(sample,variant)]=bind_sample(store,spec['collection_id'],sample,template)
        for item in spec['comparisons']:
            differences=request_differences(bound[(sample,item['baseline'])],bound[(sample,item['candidate'])],item.get('allow_command_change',False))
            if differences:raise LabError('incomparable','Case comparison conditions differ before execution',{'sample':sample,'differences':differences})
    return spec,bound


def case_budget(spec,bound):
    return {'schema_version':1,'analysis':'cases','execution_performed':False,'run_calls':len(bound),'run_budget':spec['budget'],
            'jobs':[{'sample_id':sample,'variant':variant,'each_run':execution_budget(request)} for (sample,variant),request in bound.items()],
            'estimated_runtime_seconds':None,
            'limitations':['Sample verification/hashing is preparation I/O; no program is executed by this preview.',
                           'Ordinary run lifecycle applies per job; timings/counters keep each adapter scope.',
                           'Case matrix comparisons are unpaired; use replay prepare and paired Study for block-based comparisons.']}


def run_cases(spec,bound,store):
    id,directory=store.create('studies')
    collection=read_collection(store,spec['collection_id'])
    metadata={s['id']:s for s in collection['samples']}
    result={'schema_version':1,'study_id':id,'spec':spec,'status':'running','started_at':utc(),
            'runs':[],'comparisons':[],'growth':{},'secondary_growth':{},'error':None,
            'execution_budget':case_budget(spec,bound),'cases':[{'sample_id':s,'status':'pending','capture_result':metadata[s]['result'],
              'capture_correctness':metadata[s]['correctness'],'capture_observation':metadata[s]['observation']} for s in spec['samples']]}
    from .lifecycle import StudyProgress,notify_record_created
    progress=StudyProgress(result,directory,bound.values(),result['execution_budget']['run_calls'])
    write_json(directory/'study.json',result)
    jobs=list(bound);random.Random(spec['seed']).shuffle(jobs);result['scheduled_jobs']=[{'sample_id':s,'variant':v} for s,v in jobs]
    measured={}
    try:
        notify_record_created('study',id)
        for index,(sample,variant) in enumerate(jobs):
            result['active_job']={'sample_id':sample,'variant':variant,'index':index}
            for row in result['cases']:
                if row['sample_id']==sample:row['status']='running'
            write_json(directory/'study.json',result)
            request=copy.deepcopy(bound[(sample,variant)]);request['name']=spec['name']+'/'+sample+'/'+variant
            with progress.delegate('run',dict(result['active_job'])):
                native=run(request,store)
            result['runs'].append({'sample_id':sample,'variant':variant,'run_id':native['run_id'],'status':native['status']})
            write_json(directory/'study.json',result)
            if native['status']!='completed':
                details={'sample_id':sample,'variant':variant,'run_id':native['run_id'],'error':native['error']}
                if 'acceptance' in native['error']['details']:details['acceptance']=native['error']['details']['acceptance']
                raise LabError(native['error']['category'],'Case member failed; input retained and no successful subset is scored',details)
            measured[(sample,variant)]=native
            if all((sample,v) in measured for v in spec['requests']):
                for row in result['cases']:
                    if row['sample_id']==sample:row['status']='completed'
        comparisons=[]
        for sample in spec['samples']:
            for item in spec['comparisons']:
                c=compare(measured[(sample,item['baseline'])],measured[(sample,item['candidate'])],item['metric'],
                          item.get('allow_command_change',False),policy=item['policy'],metric_scope=item.get('metric_scope'))
                c.update(case_id=sample,collection_id=spec['collection_id'],study_id=id,
                         baseline_variant=item['baseline'],candidate_variant=item['candidate'])
                comparisons.append(c)
        for comparison in comparisons:
            cid,path=store.create('comparisons');comparison['comparison_id']=cid
            write_json(path/'comparison.json',comparison);result['comparisons'].append(comparison)
        result['status']='completed'
    except LabError as error:
        result['status']='cancelled' if error.category=='cancelled' else 'failed';result['error']=error.as_dict()
    except KeyboardInterrupt:result['status']='cancelled';result['error']=LabError('cancelled','Case study cancelled').as_dict()
    except OSError as error:result['status']='failed';result['error']=LabError('storage_failed',str(error)).as_dict()
    except Exception as error:
        result['status']='failed';result['error']=LabError('internal_error',str(error)).as_dict();raise
    finally:
        progress.terminal()
        if result['status'] in ('failed','cancelled'):
            for row in result['cases']:
                if row['status']=='running':row['status']=result['status']
        result['active_job']=None;result['ended_at']=utc();write_json(directory/'study.json',result)
    return result
