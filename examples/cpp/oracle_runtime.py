#!/usr/bin/env python3.11
"""Actual copied ELF/RUNPATH and dlopen checks; no fabricated map observations."""
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from cppperflab.execution import execute
from cppperflab.protocol import sha256

loader,origin,candidate,optional=map(lambda p:Path(p).resolve(),sys.argv[1:])
cfg=dict(stages=['preflight'],interval_ms=10,max_snapshots=30,max_processes=8)
with tempfile.TemporaryDirectory() as d:
    root=Path(d);copy=root/'copied-program';shutil.copy2(loader,copy)
    expected=dict(basename=candidate.name,path=str(candidate),sha256=sha256(candidate))
    cfg['required_objects']=[expected]
    for name,environment,status,label in [('stale',{},'FAIL','origin'),('desired',{'LD_LIBRARY_PATH':str(candidate.parent)},'PASS','candidate')]:
        folder=root/name
        r=execute([str(copy),'hold','120'],folder,env=environment,timeout=3,runtime_identity=cfg,identity_target=str(copy),identity_direct=True)
        assert r['status']=='completed' and r['returncode']==0
        report=json.loads((folder/'runtime-identity.json').read_text())
        assert report['acceptance']['status']==status,report['acceptance']
        assert ('value=7 library='+label) in (folder/'stdout.txt').read_text()
    cfg['required_objects']=[dict(basename=optional.name,path=str(optional),sha256=sha256(optional))]
    folder=root/'dlopen'
    r=execute([str(copy),'plugin',str(optional),'120'],folder,timeout=3,runtime_identity=cfg,identity_target=str(copy),identity_direct=True)
    report=json.loads((folder/'runtime-identity.json').read_text())
    assert r['returncode']==0 and report['acceptance']['status']=='PASS',report['acceptance']
print('Actual copied ELF stale/desired library identities and dlopen evidence passed')
