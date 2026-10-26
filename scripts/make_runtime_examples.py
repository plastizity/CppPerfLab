#!/usr/bin/env python3.11
"""Generate synthetic ELF/library identity requests; never builds or edits a business project."""
import copy
import json
from pathlib import Path
import shutil

root=Path(__file__).resolve().parents[1]
binary=root/'build/profile/examples/cpp'
local=root/'.perflab/example-inputs/runtime-identity';local.mkdir(parents=True,exist_ok=True)
loader=local/'copied-loader';shutil.copy2(binary/'runtime_loader',loader)
origin=binary/'runtime-origin/libperflab_runtime.so'
candidate=binary/'runtime-candidate/libperflab_runtime.so'
optional=binary/'libruntime_optional.so'
import sys
sys.path.insert(0,str(root/'src'))
from cppperflab.protocol import sha256

def expectation(path):return dict(basename=path.name,path=str(path),sha256=sha256(path))
request=dict(schema_version=1,name='synthetic-loaded-candidate',
    workload=dict(id='synthetic-runtime-module-identity',parameters=dict(hold_ms=200,purpose='identity verification; no speedup claim'),inputs=[]),
    target=dict(argv=[str(loader),'hold','200'],environment={'LD_LIBRARY_PATH':str(candidate.parent)},
        build=dict(revision='reviewed synthetic runtime fixture',compiler='unknown; see compile_commands.json',flags=[],mode='RelWithDebInfo',
            artifacts=[str(root/'examples/cpp/runtime_loader.cpp'),str(root/'examples/cpp/runtime_plugin.cpp'),str(origin),str(candidate),str(optional),str(root/'build/profile/compile_commands.json')])),
    measurement=dict(tool='perf-stat',options=dict(events=['duration_time']),repetitions=2,warmups=0,timeout_seconds=15,
        runtime_identity=dict(stages=['preflight','measurement'],interval_ms=10,max_snapshots=80,max_processes=16,required_objects=[expectation(candidate)])),
    correctness=dict(argv=[str(loader),'--verify'],description='Independent synthetic integer oracle expects linked value 7; plugin mode separately checks optional value 9'))
stale=copy.deepcopy(request);stale['name']='synthetic-stale-rpath';stale['target']['environment']={}
plugin=copy.deepcopy(stale);plugin['name']='synthetic-dlopen-observation';plugin['target']['argv']=[str(loader),'plugin',str(optional),'200']
plugin['measurement']['runtime_identity']['required_objects']=[expectation(origin),expectation(optional)]
preflight=copy.deepcopy(request);preflight['name']='synthetic-preflight-observation';preflight['measurement']['runtime_identity']['stages']=['preflight']
workloads=root/'workloads';workloads.mkdir(exist_ok=True)
for filename,data in [('runtime-loaded-good',request),('runtime-loaded-stale',stale),('runtime-dlopen',plugin),('runtime-preflight-only',preflight)]:
    path=workloads/(filename+'.json');path.write_text(json.dumps(data,indent=2)+'\n');print(path)
