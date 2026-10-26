#!/usr/bin/env python3.11
"""Round-trip the real compiled SDK fixture through the production parser."""
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from cppperflab.phases import normalize

with tempfile.TemporaryDirectory() as d:
    p=Path(d)/'sdk.phases.json'
    subprocess.run([sys.argv[1],str(p)],check=True)
    r=normalize(p)
assert len(r['phases'])==2
assert r['phases'][1]['path']==['outer','inner"quoted']
assert r['phases'][1]['input_id']=='text\ninput'
assert r['phases'][1]['self_seconds'] is None
assert r['phases'][0]['self_seconds'] is not None
assert not r['correctness_failed']
assert all(m['evidence_state']=='Modeled' for m in r['metrics'] if m['name']=='phase.work')
print('Compiled SDK / production parser contract passed')
