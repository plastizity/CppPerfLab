#!/usr/bin/env python3.11
"""Prepare synthetic application activity requests, without executing targets."""
import copy
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1];out=root/'workloads';out.mkdir(exist_ok=True)
binary=root/'build/profile/examples/cpp/thread_activity'
request=dict(schema_version=1,name='synthetic-two-thread-application-activity',
    workload=dict(id='synchronized-cyclic-sums',parameters=dict(n=100000000),inputs=[]),
    target=dict(argv=[str(binary),'100000000','{run_dir}/activity.phases.json'],environment={},
        build=dict(revision='reviewed synthetic synchronized integer loops',compiler='see compile_commands.json',flags=[],
            mode='RelWithDebInfo',artifacts=[str(root/'examples/cpp/thread_activity.cpp'),str(root/'include/cppperflab/phase.hpp'),
                str(root/'build/profile/compile_commands.json')])),
    correctness=dict(argv=[str(binary),'--verify'],description='Independent closed-form integer oracle; every work call also checked'),
    measurement=dict(tool='perf-stat',repetitions=3,warmups=0,timeout_seconds=30,phase_report='activity.phases.json',options=dict(events=['duration_time'])),
    goal=dict(metrics=['time.wall','phase.application_wait_wall'],max_overhead='high',allow_instrumentation=True))
sampling=copy.deepcopy(request);sampling['name']='synthetic-two-thread-activity-and-cpu-samples'
sampling['measurement'].update(tool='perf-record',repetitions=1,options=dict(frequency=199,call_graph='fp',sample_trace=True,symbolizer='addr2line',
    precheck=dict(min_samples=5,min_resolved_self_fraction=.7,min_resolved_stack_fraction=0,require_phase_clock=True)))
sampling['goal']['metrics']=['cpu.hotspots','phase.application_wait_wall']
for name,value in [('thread-activity',request),('thread-activity-sampling',sampling)]:
    path=out/(name+'.json');path.write_text(json.dumps(value,indent=2)+'\n');print(path)
