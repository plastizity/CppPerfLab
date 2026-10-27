"""Generate standalone DME studies; do not build, probe, or execute targets."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blocks',type=int,default=3)
    parser.add_argument('--sizes',type=int,nargs='+',default=[32768,262144])
    parser.add_argument('--rounds',type=int,default=8)
    parser.add_argument('--output',type=Path,default=Path('workloads/dme'))
    args=parser.parse_args()
    if args.blocks<2 or args.rounds<1 or any(n<1 for n in args.sizes):
        parser.error('Need at least two blocks, positive sizes and rounds')
    root=Path(__file__).resolve().parents[1];out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    binary=root/'build/profile/examples/cpp/data_movement'
    oracle=root/'examples/cpp/oracle_data_movement.py'
    policy=dict(schema_version=1,min_samples=args.blocks,max_cv=.5,max_regression_fraction=.1)
    def save(name,value):
        path=out/name;path.write_text(json.dumps(value,indent=2)+'\n');return str(path)
    studies=[]
    for scenario,left,right in [('pointer','random','seq'),('layout','aos','soa'),('aa','seq','seq')]:
        for size in (args.sizes[:1] if scenario=='aa' else args.sizes):
            for profile in (('counts',) if scenario=='aa' else ('native','counts')):
                label=f'{scenario}-{size}-{profile}'
                options={'events':['duration_time']} if profile=='native' else {
                    'events':['instructions','cache-references','cache-misses'],
                    'event_groups':[['instructions','cache-references','cache-misses']]}
                requests={}
                for variant,mode in [('baseline',left),('candidate',right)]:
                    request={'schema_version':1,'name':label+'/'+variant,
                        'workload':{'id':'synthetic-sum-items-v1','parameters':{
                            'size':size,'rounds':args.rounds,'seed':71,'threads':1},'inputs':[]},
                        'target':{'argv':[str(binary),mode,str(size),str(args.rounds),'71','{run_dir}/invocation.work.json'],
                            'environment':{},'build':{'revision':'standalone synthetic DME workload',
                            'compiler':'see compile_commands.json','flags':['-O2','-g','-DNDEBUG','-fno-fast-math','-ffp-contract=off'],
                            'mode':'RelWithDebInfo','artifacts':[str(root/'examples/cpp/data_movement.cpp'),
                                str(oracle),str(root/'build/profile/compile_commands.json')]}},
                        'measurement':{'tool':'perf-stat','repetitions':1,'warmups':0,'timeout_seconds':30,
                            'options':options,'work_report':{'schema_version':1,'path':'invocation.work.json','definition_id':'sum-items-v1'}},
                        'correctness':{'argv':['/usr/bin/python3.11',str(oracle),str(binary)],
                            'description':'Independent integer closed-form oracle and work-report checks across all four synthetic modes; target also validates every invocation.'}}
                    if profile=='native':
                        request['claim']={'schema_version':1,'hypothesis':'Reduce full task cost under an unchanged exact integer task.',
                            'kind':'task_cost','applies_to':'This synthetic input size, seed, rounds, and recorded machine only.',
                            'allowed_changes':['access pattern or layout including setup costs'],'metric':'time.wall',
                            'conditions':[{'id':'oracle','source':'correctness','required':True,'description':'Declared independent oracle passes.'},
                                {'id':'work','source':'work_metric','required':True,'metric':'work.items',
                                    'description':'Same target-reported completed summation items; not a statement that setup work or instructions are equal.'}]}
                    requests[variant]=save(label+'-'+variant+'.request.json',request)
                primary='time.wall' if profile=='native' else 'cpu.cache_misses'
                secondary=['work.items']+(['cpu.instructions','cpu.cache_references'] if profile=='counts' else [])
                spec={'schema_version':1,'analysis':'paired','name':label,**requests,
                    'blocks':args.blocks,'order':'abba','seed':71,'budget':args.blocks*4,'metric':primary,
                    'policy':policy,'secondary_metrics':[{'metric':m,'policy':policy} for m in secondary],
                    'constraints':'Exact same summation task. Whole invocation includes construction/allocation/reporting. Cache counts are informational; no causal or bandwidth claim.',
                    'resource_conditions':{'concurrent_runs':1,'background_load':'uncontrolled',
                        'notes':'Serial platform execution; cache state and host/guest scheduling uncontrolled; no background isolation.'},
                    'allow_command_change':True}
                if scenario=='aa':spec['allow_command_change']=False
                studies.append(save(label+'.study.json',spec))
    save('manifest.json',{'schema_version':1,'studies':studies,'run_calls':len(studies)*args.blocks*4,
        'formal_samples':len(studies)*args.blocks*4,'note':'Each Run additionally performs oracle, target preflight and actual tool probe. No warmups. Inspect perflab budget before executing. Three blocks demonstrate interfaces, not a universal confidence policy.'})
    print(out/'manifest.json')


if __name__=='__main__':main()
