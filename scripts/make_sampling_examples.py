#!/usr/bin/env python3.11
"""Generate reviewed synthetic sampling/precheck requests; no builds or target execution."""
import copy
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1];out=root/'workloads';out.mkdir(exist_ok=True)
binary=root/'build/profile/examples/cpp'
request=dict(schema_version=1,name='synthetic-phase-cpu-sampling',
    workload=dict(id='cyclic-integer-kernels',parameters=dict(n=60000000,calls=6),inputs=[]),
    target=dict(argv=[str(binary/'phase_cpu'),'60000000','6','{run_dir}/cpu.phases.json'],environment={},
        build=dict(revision='reviewed synthetic integer kernels',compiler='see compile_commands.json',flags=[],mode='RelWithDebInfo',
            artifacts=[str(root/'examples/cpp/phase_cpu.cpp'),str(root/'include/cppperflab/phase.hpp'),str(root/'build/profile/compile_commands.json')])),
    correctness=dict(argv=[str(binary/'phase_cpu'),'--verify'],description='Independent integer closed-form oracles over boundary sizes; every target call is separately validated'),
    measurement=dict(tool='perf-record',repetitions=2,warmups=0,timeout_seconds=30,phase_report='cpu.phases.json',
        options=dict(frequency=199,call_graph='fp',sample_trace=True,symbolizer='addr2line',
            precheck=dict(min_samples=20,min_resolved_self_fraction=.8,min_resolved_stack_fraction=.5,require_phase_clock=True))),
    goal=dict(metrics=['cpu.hotspots'],max_overhead='high',allow_instrumentation=True))
stripped=copy.deepcopy(request);stripped['name']='synthetic-unresolved-sampling-negative-control'
stripped['target']['argv'][0]=str(binary/'phase_cpu_stripped');stripped['correctness']['argv'][0]=str(binary/'phase_cpu_stripped')
unsupported=copy.deepcopy(request);unsupported['name']='synthetic-native-perf-readiness'
unsupported['measurement']['options'].update(call_graph='dwarf',symbolizer='perf')
for name,data in [('sampling-phases',request),('sampling-unresolved',stripped),('sampling-native-readiness',unsupported)]:
    p=out/(name+'.json');p.write_text(json.dumps(data,indent=2)+'\n');print(p)
