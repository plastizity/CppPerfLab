"""Serial D01-D05 CLI validation using reviewed, explicitly built synthetic targets."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
from cppperflab.storage import Store,write_json,utc
from cppperflab.protocol import sha256


def main():
    store=Store(root/'.perflab');vid,out=store.create('validations')
    exe=root/'.local/bin/perflab';binary=root/'build/profile/examples/cpp'
    env=dict(os.environ,CPPPERFLAB_TOOL_DIR=str(root/'vendor/hyperfine-1.19.0'))
    compiler=subprocess.check_output(['c++','--version'],text=True).splitlines()[0]
    controls={'schema_version':1,'min_samples':7,'max_cv':.1,'max_regression_fraction':.05,'min_speedup':1.01}
    write_json(out/'policy.json',controls)
    write_json(out/'allocation-policy.json',{**controls,'direction':'lower'})
    manifest=dict(schema_version=1,validation_id=vid,status='running',started_at=utc(),runs={},comparisons={},studies={},
                  cli_sha256=sha256(exe),driver_sha256=sha256(__file__),
                  limitations=['Synthetic workloads; WSL host interference and PMU fidelity uncontrolled.',
                               'Callgrind/DHAT are intrusive; their timings do not indicate native performance.',
                               'Cachegrind and logical intensity are Modeled; no measured acceptance score.'])
    write_json(out/'manifest.json',manifest)
    step=0
    def call(*args,expected_code=0):
        nonlocal step
        step+=1
        r=subprocess.run([str(exe),*args,'--store',str(store.root),'--json'],cwd=root,env=env,capture_output=True,text=True)
        p=out/f'{step:03}-{args[0]}.json';p.write_text(r.stdout);p.with_suffix('.stderr.txt').write_text(r.stderr)
        e=json.loads(r.stdout)
        if r.returncode!=expected_code:raise RuntimeError('Unexpected CLI result; see '+str(p))
        if expected_code==0 and not e['ok']:raise RuntimeError(str(e['error']))
        return e['result']
    def config(name,tool,argv,workload,options=None,repetitions=7,oracle=None,artifacts=None):
        request=dict(schema_version=1,name=name,workload=dict(id=workload[0],parameters=workload[1],inputs=[]),
                     target=dict(argv=list(map(str,argv)),environment={'OMP_NUM_THREADS':'1'},build=dict(revision='reviewed synthetic source fingerprints',
                         compiler=compiler,flags=['-O2','-g','-DNDEBUG','-fno-fast-math','-ffp-contract=off'],mode='RelWithDebInfo',
                         artifacts=list(map(str,artifacts or [root/'examples/cpp/workload.cpp',root/'build/profile/compile_commands.json'])))),
                     correctness=dict(argv=list(map(str,oracle or ['ctest','--test-dir',root/'build/profile','--output-on-failure'])),
                                      description='Independent exact fixture oracles; numerical kernel uses integer dyadic reference at ten sizes'),
                     measurement=dict(tool=tool,repetitions=repetitions,warmups=0,timeout_seconds=120,
                                      cpu_affinity=[min(os.sched_getaffinity(0))],options=options or {}))
        if tool=='hyperfine':request['measurement']['warmups']=2
        path=out/(name+'.request.json');write_json(path,request);return path
    def run(name,path):
        r=call('run','--config',str(path));manifest['runs'][name]=r['run_id'];write_json(out/'manifest.json',manifest)
        print(name,r['run_id'],flush=True);return r['run_id']
    def compare(name,a,b,metric='time.wall',policy='policy.json'):
        c=call('compare',a,b,'--metric',metric,'--policy',str(out/policy))
        manifest['comparisons'][name]=c['comparison_id'];write_json(out/'manifest.json',manifest)
        print(name,c['acceptance']['status'],flush=True)
    try:
        for domain in ('D01','D02','D03','D04','D05'):call('metrics','--domain',domain)
        for tool in ('callgrind','dhat','cachegrind'):
            probe=call('tools','probe','--tool',tool)
            if not probe['tools'][tool]['available']:raise RuntimeError(tool+' unavailable; '+str(probe['tools'][tool]))
        rid=run('calls',config('calls','callgrind',[binary/'baseline','compute',17,200],('compute-calls',{'steps':17,'repeats':200}),repetitions=1))
        rows=call('analyze',rid,'--section','call_edges','--symbol','compute(')['data']
        assert sum(v['calls'] for v in rows if v['callee'].startswith('compute('))==600
        for label in ('baseline','candidate'):
            rid=run('kernel-'+label,config('kernel-'+label,'google-benchmark',[binary/('kernel_'+label)],('dyadic-dot',{'n':4096}),
                    options=dict(filter='^Dot/4096$',min_time_seconds=.05,warmup_seconds=.02,
                                 counters=dict(useful_flops='useful_flops_per_iteration',logical_bytes='logical_bytes_per_iteration',operations='operations_per_iteration')),
                    repetitions=9,oracle=[binary/('kernel_'+label),'--verify'],
                    artifacts=[root/'examples/cpp/benchmark_kernel.cpp',root/'build/profile/compile_commands.json']))
            call('analyze',rid,'--section','metrics','--metric','kernel.useful_flop_rate')
        compare('kernel-time',manifest['runs']['kernel-baseline'],manifest['runs']['kernel-candidate'],'time.iteration.wall')
        for label in ('baseline','candidate'):
            rid=run('heap-'+label,config('heap-'+label,'dhat',[binary/label,'allocations',3000,4],('allocation-fixture',{'blocks':3000,'repeats':4}),{'operation_count':4}))
            call('analyze',rid,'--section','allocation_sites','--symbol','main')
        compare('allocation-count',manifest['runs']['heap-baseline'],manifest['runs']['heap-candidate'],'memory.allocations','allocation-policy.json')
        compare('heap-peak',manifest['runs']['heap-baseline'],manifest['runs']['heap-candidate'],'memory.heap_peak_live')
        for label in ('baseline','candidate'):
            run('allocation-time-'+label,config('allocation-time-'+label,'hyperfine',[binary/label,'allocations',3000,64],
                                               ('allocation-fixture-native',{'blocks':3000,'repeats':64}),repetitions=9))
        compare('allocation-time',manifest['runs']['allocation-time-baseline'],manifest['runs']['allocation-time-candidate'])
        space=config('space-template','dhat',[binary/'baseline','allocations',128,2],('allocation-scale',{'blocks':128,'repeats':2}),{'operation_count':2})
        spec=dict(schema_version=1,name='allocation space growth',template=str(space),
                  axis=dict(parameter='blocks',values=[128,256,512,1024],argument_index=2),
                  variants=[dict(id=label,executable=str(binary/label),revision='reviewed '+label,
                                 artifacts=[str(root/'examples/cpp/workload.cpp'),str(root/'build/profile/compile_commands.json')]) for label in ('baseline','candidate')],
                  metric='memory.heap_peak_live',secondary_metrics=['memory.allocations','memory.allocated_bytes'],budget=8,seed=20261014,
                  constraints='Same data generation, lifecycle and two repeated operations; exact checksum required')
        write_json(out/'space.study.json',spec)
        s=call('study','--config',str(out/'space.study.json'));manifest['studies']['space']=s['study_id'];write_json(out/'manifest.json',manifest)
        call('analyze',s['study_id'],'--kind','study','--section','growth','--metric','memory.allocations')
        print('space study',s['study_id'],flush=True)
        # Reuse the existing time-scale configuration rather than a new evaluator.
        s=call('study','--config',str(root/'workloads/algorithm-study.json'));manifest['studies']['time']=s['study_id'];write_json(out/'manifest.json',manifest)
        print('time study',s['study_id'],flush=True)
        for label in ('baseline','candidate'):
            rid=run('cache-model-'+label,config('cache-model-'+label,'cachegrind',[binary/label,'locality',256,16],
                    ('layout-model',{'side':256,'repeats':16}),repetitions=1))
            q=call('analyze',rid,'--section','observations','--metric','cachegrind.d1_miss_rate')
            assert q['data'][0]['evidence_state']=='Modeled'
        rejected=call('compare',manifest['runs']['cache-model-baseline'],manifest['runs']['cache-model-candidate'],
                      '--metric','cachegrind.d1_miss_rate',expected_code=7)
        assert rejected['acceptance']['status']=='INCONCLUSIVE' and 'speedup' not in rejected
        manifest['model_rejection_comparison_id']=rejected['comparison_id']
        probe=call('tools','probe','--tool','perf-stat','--events','instructions,cache-references,cache-misses,duration_time')
        manifest['cache_pmu_probe_id']=probe['probe_id']
        if probe['tools']['perf-stat']['available']:
            for label in ('baseline','candidate'):
                run('cache-hardware-'+label,config('cache-hardware-'+label,'perf-stat',[binary/label,'locality',1024,64],
                    ('layout-hardware',{'side':1024,'repeats':64}),{'events':['instructions','cache-references','cache-misses','duration_time']}))
        else:manifest['cache_hardware_state']='Unsupported'
        # Native layout benefit has independent process-timing evidence.
        for label in ('baseline','candidate'):
            run('layout-time-'+label,root/f'workloads/locality-{label}.json')
        compare('layout-time',manifest['runs']['layout-time-baseline'],manifest['runs']['layout-time-candidate'])
        manifest['status']='completed'
    except Exception as exc:
        manifest['status']='failed';manifest['error']=str(exc);raise
    finally:
        manifest['ended_at']=utc();write_json(out/'manifest.json',manifest)
        print('manifest',out/'manifest.json',flush=True)


if __name__=='__main__':main()
