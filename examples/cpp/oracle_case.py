#!/usr/bin/env python3.11
"""Compiled adapter lifecycle/protocol tests; no fabricated performance samples."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from cppperflab.phases import normalize

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    for format,data in [('text',b'x hello xx\n'),('json',b'{"x":"xx","array":[1,2]}'),('binary',b'\x00x\xffx')]:
        input=root/'input';expected=root/'expected';report=root/'result.phases.json'
        input.write_bytes(data);expected.write_text(str(data.count(b'x')))
        for lifecycle in ('per_call','reuse_reset'):
            args=[sys.argv[1],'--case-v1',str(input),str(expected),'case-'+format,format,'raw-bytes-v1','text','count-v1',lifecycle,'3',str(report)]
            subprocess.run(args,check=True,capture_output=True)
            result=normalize(report)
            assert not result['correctness_failed']
            paths=[tuple(p['path']) for p in result['phases']]
            assert paths.count(('load',))==paths.count(('unload',))==1
            assert paths.count(('operation','invoke'))==3
            assert paths.count(('operation','validate'))==3
            if lifecycle=='per_call':assert paths.count(('operation','prepare'))==3 and ('prepare',) not in paths
            else:assert paths.count(('prepare',))==1 and paths.count(('operation','reset'))==3
            work=[m for m in result['metrics'] if m['name']=='phase.work' and m['scope']=='case-lifecycle; phase=["operation","invoke"]; counter="input_bytes"']
            assert len(work)==1 and work[0]['value']==len(data)*3
        no_expected=list(args);no_expected[3]='-';no_expected[7:9]=['-','-']
        subprocess.run(no_expected,check=True,capture_output=True)
        assert not normalize(report)['correctness_failed']
        expected.write_text(str(data.count(b'x')+1))
        failed=subprocess.run(args,capture_output=True)
        assert failed.returncode==2
        result=normalize(report)
        assert result['correctness_failed']
        assert sum(p['path']==['operation','invoke'] for p in result['phases'])==1
print('Compiled file adapters: text/JSON/binary, both lifecycles, independent validation, failure stop passed')
