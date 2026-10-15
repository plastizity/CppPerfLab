#!/usr/bin/env python3.11
"""Generate local configs for reviewed synthetic pipeline and strong-scaling examples."""
import json
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
build=root/'build/profile'
bin_dir=build/'examples/cpp'
compiler=subprocess.check_output(['/usr/bin/c++','--version'],text=True).splitlines()[0]
def save(name,data):
    path=root/'workloads'/name
    path.write_text(json.dumps(data,indent=2)+'\n')
    print(path)
def request(name,executable,source,parameters,argv,tool,options):
    return dict(schema_version=1,name=name,workload=dict(id=name,parameters=parameters,inputs=[]),
        target=dict(argv=[str(bin_dir/executable),*argv],environment={},build=dict(
            revision='reviewed synthetic source; artifact hashes authoritative',compiler=compiler,
            flags=['-O2','-g','-DNDEBUG'],mode='RelWithDebInfo',artifacts=[str(root/'examples/cpp'/source),str(build/'compile_commands.json')])),
        measurement=dict(tool=tool,repetitions=9,warmups=0,timeout_seconds=120,options=options),
        correctness=dict(argv=[str(bin_dir/executable),'--verify'],description='Exact integer synthetic oracle; timed workload also checks each output'))
pipeline=request('synthetic-copy-pipeline','pipeline_benchmark','benchmark_pipeline.cpp',{'n':1048576},[],
    'google-benchmark',dict(filter='^Pipeline/1048576/real_time$',min_time_seconds=.1,warmup_seconds=.02,
        counters=dict(copied_bytes='copied_bytes',traversals='traversals',operations='operations'),
        stage=dict(name='copy',seconds_counter='copy_seconds')))
save('science-pipeline.json',pipeline)
parallel=request('synthetic-parallel-reduction','parallel_reduce','parallel_reduce.cpp',
    dict(workers=1,n=4194304,repeats=32),['1','4194304','32'],'hyperfine',{})
save('science-parallel-template.json',parallel)
save('science-strong-scaling.json',dict(schema_version=1,name='synthetic-fixed-work-scaling',template='science-parallel-template.json',
    analysis='strong_scaling',axis=dict(parameter='workers',values=[1,2,4],argument_index=1),
    variants=[dict(id='reduction',executable=str(bin_dir/'parallel_reduce'),revision='reviewed synthetic source',
        artifacts=parallel['target']['build']['artifacts'])],budget=3,seed=20261015,metric='time.wall',
    constraints='Identical 4194304 uint64 elements and 32 full reductions; only requested worker count varies. Includes allocation, initialization, launch and final reduction; no CPU affinity restriction.'))

import sys
for method in ('jacobi','gs'):
    solver=request('synthetic-spd-1d','solver_'+method,'solver.cpp',
        dict(n=65536,tolerance=1e-8,max_iterations=100),['65536','1e-8','100','{run_dir}/solve.solver.json'],
        'perf-stat',dict(events=['duration_time']))
    solver['target']['build']['flags'] += ['-fno-fast-math','-ffp-contract=off']
    if method=='gs':solver['target']['build']['flags'].append('-DSOLVER_GS=1')
    solver['correctness']['description']='Synthetic SPD preflight solve checks; independent per-solve residual and solution-error recomputation'
    solver['target']['build']['artifacts'].append(str(root/'examples/cpp/oracle_solver.py'))
    solver['correctness']['sample_validation']=dict(
        argv=[sys.executable,str(root/'examples/cpp/oracle_solver.py'),'{sample_dir}/solve.solver.json','65536'],
        report='solve.solver.json',residual_norm='l2_absolute',max_residual=1e-8,
        max_absolute_error=1e-7,max_relative_error=1e-9,max_iterations=100)
    save('science-solver-'+method+'.json',solver)
