#!/usr/bin/env python3.11
"""Generate standalone diagnostic requests; no compilation or target execution."""
import copy
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1];out=root/'workloads';out.mkdir(exist_ok=True)
binary=root/'build/profile/examples/cpp/phase_cpu'
request=dict(schema_version=1,name='synthetic-live-progress',
    workload=dict(id='cyclic-integer-kernels',parameters=dict(n=60000000,calls=6),inputs=[]),
    target=dict(argv=[str(binary),'60000000','6','{run_dir}/cpu.phases.json'],environment={},
        build=dict(revision='reviewed synthetic integer kernels',compiler='see compile_commands.json',flags=[],mode='RelWithDebInfo',
            artifacts=[str(root/'examples/cpp/phase_cpu.cpp'),str(root/'include/cppperflab/phase.hpp'),str(root/'build/profile/compile_commands.json')])),
    correctness=dict(argv=[str(binary),'--verify'],description='Independent integer closed-form oracles; every target call is validated'),
    measurement=dict(tool='perf-stat',repetitions=5,warmups=1,timeout_seconds=30,progress=True,phase_report='cpu.phases.json',options=dict(events=['duration_time'])),
    goal=dict(metrics=['time.wall'],max_overhead='high',allow_instrumentation=False))
candidate=copy.deepcopy(request);candidate['name']='synthetic-live-progress-later-run';candidate['measurement'].update(repetitions=3,warmups=0)
rejected=copy.deepcopy(request);rejected['name']='synthetic-live-progress-overhead-rejection';rejected['goal']['max_overhead']='low'
for name,data in [('status-progress',request),('status-later',candidate),('status-overhead-rejected',rejected)]:
    path=out/(name+'.json');path.write_text(json.dumps(data,indent=2)+'\n');print(path)
