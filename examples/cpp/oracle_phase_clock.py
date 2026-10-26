#!/usr/bin/env python3.11
"""Compile-backed proof of clock/namespace/thread metadata, without CPU sample mocks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from cppperflab.phases import normalize
with tempfile.TemporaryDirectory() as d:
    report=Path(d)/'cpu.phases.json';start=time.monotonic_ns()
    child=subprocess.Popen([sys.argv[1],'100001','3',str(report)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    _,error=child.communicate(timeout=5);stop=time.monotonic_ns()
    assert child.returncode==0,error
    result=normalize(report);clock=result['clock']
    assert clock['name']=='CLOCK_MONOTONIC'
    assert clock['tracks']['main']==dict(pid=child.pid,tid=child.pid)
    assert clock['pid_namespace']==os.readlink('/proc/self/ns/pid')
    assert clock['time_namespace']==os.readlink('/proc/self/ns/time')
    assert len(result['phases'])==9 and not result['correctness_failed']
    for p in result['phases']:
        assert start<=p['absolute_start_ns']<=p['absolute_end_ns']<=stop
        assert p['pid']==p['tid']==child.pid
print('Actual CLOCK_MONOTONIC phase intervals, OS thread/PID and namespaces passed')
