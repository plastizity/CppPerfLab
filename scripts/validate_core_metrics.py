"""Serial CLI acceptance driver for prebuilt, reviewed synthetic examples.

Run make_examples.py first. No building, installation, alternate executor or DB.
Raw CLI envelopes, preregistered policy and manifest are stored in the same Store.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
from cppperflab.storage import Store, write_json, utc
from cppperflab.protocol import sha256


def main():
    store=Store(root/'.perflab')
    identifier,directory=store.create('validations')
    cli=root/'.local/bin/perflab'
    environment=dict(os.environ,CPPPERFLAB_TOOL_DIR=str(root/'vendor/hyperfine-1.19.0'))
    manifest={'schema_version':1,'validation_id':identifier,'started_at':utc(),
              'status':'running','runs':{},'comparisons':{},'queries':[],
              'cli_sha256':sha256(cli),'driver_sha256':sha256(__file__),
              'policy':{'schema_version':1,'min_samples':7,'max_cv':.1,
                        'max_regression_fraction':.05,'min_speedup':1.05},
              'limitations':['Synthetic examples only; WSL host load and PMU fidelity uncontrolled.',
                             'Oracle coverage is declared preflight coverage, not per-sample numerical validation.',
                             'Serial A/B/A/B batches are not a paired bootstrap design.']}
    write_json(directory/'policy.json',manifest['policy'])
    # Instruction count has its own declared direction; it is not elapsed speedup.
    write_json(directory/'instructions-policy.json',{**manifest['policy'],'direction':'lower'})
    write_json(directory/'manifest.json',manifest)
    counter=0

    def call(*args):
        nonlocal counter
        counter+=1
        result=subprocess.run([str(cli),*args,'--store',str(store.root),'--json'],
                              cwd=root,env=environment,capture_output=True,text=True)
        path=directory/('%03d-%s.json'%(counter,args[0]))
        path.write_text(result.stdout)
        path.with_suffix('.stderr.txt').write_text(result.stderr)
        envelope=json.loads(result.stdout)
        if result.returncode or not envelope['ok']:
            raise RuntimeError('CLI failed; retained '+str(path))
        return envelope['result']

    def measure(name, config):
        result=call('run','--config',str(config))
        manifest['runs'][name]=result['run_id']
        write_json(directory/'manifest.json',manifest)
        print(name,result['run_id'],flush=True)
        return result['run_id']

    try:
        call('metrics','--domain','D01')
        call('schema','evidence')
        probe=call('tools','probe','--tool','perf-stat','--events','instructions,cycles,branches,branch-misses')
        manifest['pmu_probe_id']=probe['probe_id']
        study=call('study','--config',str(root/'workloads/algorithm-study.json'))
        manifest['study_id']=study['study_id']
        write_json(directory/'manifest.json',manifest)
        print('study',study['study_id'],flush=True)
        call('analyze',study['study_id'],'--kind','study','--section','growth')
        if probe['tools']['perf-stat']['available']:
            for label in ('baseline','candidate'):
                config=json.loads((root/f'workloads/pair-counters-{label}.json').read_text())
                config['measurement']['options']['events']=['instructions','cycles','branches','branch-misses','duration_time']
                path=directory/f'counter-{label}.json';write_json(path,config)
                rid=measure('counter-'+label,path)
                for metric in ('cpu.ipc','cpu.cpi','control.branch_mpki','control.branch_miss_rate'):
                    query=call('analyze',rid,'--section','observations','--metric',metric)
                    manifest['queries'].append({'run_id':rid,'metric':metric,'states':sorted({r['evidence_state'] for r in query['data']})})
            comparison=call('compare',manifest['runs']['counter-baseline'],manifest['runs']['counter-candidate'],
                            '--metric','cpu.instructions','--policy',str(directory/'instructions-policy.json'))
            manifest['comparisons']['instructions']=comparison['comparison_id']
        else:
            manifest['cpu_evidence_state']='Unsupported'
        measure('heap',root/'workloads/heap-profile.json')
        call('analyze',manifest['runs']['heap'],'--section','metrics','--metric','memory.heap_peak_observed')
        for batch in (1,2):
            a=measure(f'time-baseline-{batch}',root/'workloads/locality-baseline.json')
            b=measure(f'time-candidate-{batch}',root/'workloads/locality-candidate.json')
            comparison=call('compare',a,b,'--policy',str(directory/'policy.json'))
            manifest['comparisons'][f'time-{batch}']=comparison['comparison_id']
        args=['summarize','--run',manifest['runs']['heap']]
        for cid in manifest['comparisons'].values():args+=['--comparison',cid]
        report=call(*args)
        manifest['summary_id']=report['record_id']
        manifest['status']='completed'
    except Exception as exc:
        manifest['status']='failed';manifest['error']=str(exc)
        raise
    finally:
        manifest['ended_at']=utc()
        write_json(directory/'manifest.json',manifest)
        print('manifest',directory/'manifest.json',flush=True)


if __name__=='__main__':
    main()
