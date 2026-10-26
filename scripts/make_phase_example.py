#!/usr/bin/env python3.11
"""Generate synthetic text and an ordinary run request; builds and runs nothing."""
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
inputs=root/'workloads';inputs.mkdir(exist_ok=True)
text=inputs/'phase-text-input.txt';text.write_text('x abc x 0123456789\n'*32768)
exe=root/'build/profile/examples/cpp/phase_text'
r=dict(schema_version=1,name='synthetic-text-phases',
       workload=dict(id='synthetic-text-count',parameters={'repeats':32},inputs=[str(text)]),
       target=dict(argv=[str(exe),str(text),'32','{run_dir}/text.phases.json'],environment={},
                   build=dict(revision='reviewed synthetic source',compiler='see compile_commands.json',flags=['-O2','-g','-DNDEBUG'],
                              mode='RelWithDebInfo',artifacts=[str(root/'examples/cpp/phase_text.cpp'),str(root/'include/cppperflab/phase.hpp'),str(root/'build/profile/compile_commands.json')])),
       measurement=dict(tool='perf-stat',repetitions=9,warmups=1,timeout_seconds=30,options={'events':['duration_time']},phase_report='text.phases.json'),
       correctness=dict(argv=[str(exe),'--verify'],description='Synthetic exact text fixtures before measurement; each measured call separately checked against std::count outside invocation interval'))
path=inputs/'phase-text.json';path.write_text(json.dumps(r,indent=2)+'\n');print(path)
