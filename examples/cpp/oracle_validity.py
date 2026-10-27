#!/usr/bin/env python3.11
"""Consumer-owned synthetic oracle and external-state setup/verification.

This script demonstrates a file state, not CPU cache or application snapshots.
It is trusted test code executed by the existing process boundary.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def establish(path):
    path.write_text('ready\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify(path, state_id):
    actual=path.read_text(encoding='utf-8').strip()
    passed=actual=='ready'
    print(json.dumps({'schema_version':1, 'status':'PASS' if passed else 'FAIL',
                      'description':'Consumer verifies the synthetic state file',
                      'observations':{'state_id':state_id,'actual':actual}}, sort_keys=True))
    return 0


def test_binary(binary):
    with tempfile.TemporaryDirectory(prefix='cppperflab-validity-oracle-') as folder:
        root=Path(folder)
        state=root/'state.txt'
        for size in (1,7,257):
            for rounds in (1,3):
                for strategy in ('baseline','candidate'):
                    establish(state)
                    report=root/'result.phases.json'
                    args=[str(binary),str(state),str(size),str(rounds),strategy,str(report)]
                    process=subprocess.run(args,capture_output=True,text=True,check=True)
                    result=json.loads(process.stdout)
                    expected=size*(size+1)//2*rounds
                    require(result['result']==expected,'Result differs from independent integer reference')
                    require(result['visits']==size*(rounds if strategy=='baseline' else 1),'Incorrect work count')
                    require(result['status']=='PASS','Target did not validate its result')
                    phase=json.loads(report.read_text(encoding='utf-8'))
                    require(phase['stages'][0]['correctness']=='passed','Phase correctness mismatch')
                    require(phase['stages'][0]['work']['visits']['value']==result['visits'],'Phase work count mismatch')
                    require(state.read_text(encoding='utf-8')=='used\n','Target did not consume state')
                    # A second invocation on the same state must fail, visibly.
                    require(subprocess.run(args,capture_output=True).returncode!=0,'Unreset state was accepted')


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='operation',required=True)
    for name in ('test','check'):
        action=sub.add_parser(name)
        action.add_argument('binary',type=Path)
        if name=='check': action.add_argument('state',type=Path)
    for name in ('establish','verify'):
        action=sub.add_parser(name)
        action.add_argument('state',type=Path)
        action.add_argument('state_id')
    args=parser.parse_args()
    if args.operation in ('test','check'):
        test_binary(args.binary)
        if args.operation=='check': establish(args.state)
        print('synthetic validity oracle passed')
        return 0
    if args.operation=='establish':
        establish(args.state)
        print(json.dumps({'schema_version':1,'state_id':args.state_id,'status':'PASS',
                          'description':'Consumer establishes the synthetic state file'},sort_keys=True))
        return 0
    return verify(args.state,args.state_id)


if __name__=='__main__':
    raise SystemExit(main())
