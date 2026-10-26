"""Actual owned-process observations plus isolated parser/failure controls."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.execution import execute
from cppperflab.protocol import load_request,sha256
from cppperflab.runtime_identity import executable_maps,process_stat,RuntimeObserver,validate_config
from cppperflab.storage import write_json
from test_core import request_template,comparison_result


def config(**changes):
    return dict(stages=['preflight','measurement'],interval_ms=10,max_snapshots=30,max_processes=16,**changes)


class RuntimeIdentityTests(unittest.TestCase):
    def test_parsers_preserve_paths_spaces_and_pid_start_identity(self):
        text='17 (name with (parentheses)) S 5 17 17 '+ ' '.join(['0']*15)+' 123 0\n'
        self.assertEqual(process_stat(text)['start_ticks'],123)
        maps='1000-2000 r-xp 00000000 00:02 71 /tmp/a library.so\n2000-3000 rw-p 00001000 00:02 71 /tmp/a library.so\n3000-4000 r-xp 0 00:00 0 [vdso]\n'
        rows=executable_maps(maps)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['path'],'/tmp/a library.so')
        self.assertEqual(rows[0]['inode'],71)
        self.assertTrue(executable_maps('1000-2000 r-xp 0 00:02 71 /tmp/lib.so (deleted)')[0]['ambiguous_path'])
        self.assertTrue(executable_maps('1000-2000 r-xp 0 00:02 71 /tmp/lib\\012.so')[0]['ambiguous_path'])
        for source,parser in [('broken',process_stat),('not a maps row',executable_maps)]:
            with self.assertRaises(LabError):parser(source)

    def test_config_boundary_is_strict_and_expected_paths_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            r=request_template(root);r['measurement']['runtime_identity']=config(required_objects=[dict(basename='liba.so',path='liba.so',sha256='a'*64)])
            p=root/'request.json';write_json(p,r)
            parsed=load_request(p)['measurement']['runtime_identity']
            self.assertEqual(parsed['required_objects'][0]['path'],str(root/'liba.so'))
            for field,value in [('interval_ms',0),('max_snapshots',False),('stages',['preflight','preflight']),('stages',[]),('unknown',1)]:
                cfg=config();cfg[field]=value
                with self.subTest(field=field,value=value),self.assertRaises(LabError):validate_config(cfg,root)

    def observe_python(self,root,code='import time; time.sleep(.12)',cfg=None,timeout=3):
        return execute([sys.executable,'-c',code],root,timeout=timeout,runtime_identity=cfg or config(),
                       identity_target=str(Path(sys.executable).resolve()),identity_direct=True)

    def test_actual_executable_and_loaded_files_have_raw_evidence_and_stable_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);record=self.observe_python(root)
            report=json.loads((root/'runtime-identity.json').read_text())
            self.assertEqual(record['status'],'completed');self.assertEqual(report['evidence_state'],'Measured')
            self.assertFalse(report['complete_loader_history']);self.assertGreater(report['target_snapshots'],0)
            self.assertTrue(report['actual_executables']);self.assertTrue(report['objects'])
            verified=[o for o in report['objects'] if o['hash']['evidence_state']=='Measured']
            self.assertTrue(verified)
            for o in verified:self.assertEqual(o['hash']['sha256'],sha256(o['path']))
            raw=json.loads((root/report['snapshots'][0]['path']).read_text())
            self.assertTrue(any('maps_b64' in p['raw'] for p in raw['processes']))
            self.assertTrue(any(base64.b64decode(p['raw'].get('maps_b64','')) for p in raw['processes']))

    def test_expected_actual_executor_passes_wrong_hash_fails_and_missing_is_not_pass(self):
        exe=str(Path(sys.executable).resolve())
        rule=dict(basename=Path(exe).name,path=exe,sha256=sha256(exe))
        for issue in ('good','wrong','missing'):
            with tempfile.TemporaryDirectory() as d:
                expected=copy.deepcopy(rule)
                if issue=='wrong':expected['sha256']='0'*64
                if issue=='missing':expected['basename']='unobserved-library.so'
                self.observe_python(Path(d),cfg=config(required_objects=[expected]))
                r=json.loads((Path(d)/'runtime-identity.json').read_text())
                self.assertEqual(r['acceptance']['status'],{'good':'PASS','wrong':'FAIL','missing':'INCONCLUSIVE'}[issue])

    def test_snapshot_limit_is_reported_without_stopping_target(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=config();cfg['max_snapshots']=1
            r=self.observe_python(Path(d),cfg=cfg)
            report=json.loads((Path(d)/'runtime-identity.json').read_text())
            self.assertEqual(r['returncode'],0);self.assertEqual(len(report['snapshots']),1)
            self.assertIn('max_snapshots',report['truncations'])

    def test_permission_and_process_exit_are_explicit_not_fallback_data(self):
        for kind in ('permission','exited'):
            with tempfile.TemporaryDirectory() as d:
                observer=RuntimeObserver(Path(d),config(),sys.executable)
                if kind=='permission':
                    real_open=os.open
                    def denied(path,*args,**kwargs):
                        if path=='/proc/123456':raise PermissionError('controlled access denial')
                        return real_open(path,*args,**kwargs)
                    with mock.patch('cppperflab.runtime_identity.os.open',side_effect=denied):observer.sample(123456)
                else:observer.sample(999999999)
                r=observer.finish()
                self.assertEqual(r['evidence_state'],'Unsupported' if kind=='permission' else 'Missing')
                self.assertFalse(r['objects']);self.assertEqual(r['acceptance']['status'],'NOT_REQUESTED')

    def test_file_changed_after_mapping_observation_is_invalid_without_repair(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'library';file.write_text('original')
            observer=RuntimeObserver(root,config(),sys.executable)
            s=file.stat();row=dict(path=str(file),device=[os.major(s.st_dev),os.minor(s.st_dev)],inode=s.st_ino,ambiguous_path=False)
            observer._object(row,17,123,'control.json')
            file.write_text('changed')
            r=observer.finish();self.assertEqual(r['objects'][0]['hash']['evidence_state'],'Invalid')
            self.assertEqual(file.read_text(),'changed')

    def test_wrapped_target_is_discovered_from_its_actual_process_not_wrapper_name(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            # A shell wrapper's real executor differs from the reviewed Python target.
            r=execute(['/bin/sh','-c','"$1" -c "import time; time.sleep(.15)" & wait','wrapper',sys.executable],root,
                timeout=3,runtime_identity=config(),identity_target=str(Path(sys.executable).resolve()))
            report=json.loads((root/'runtime-identity.json').read_text())
            self.assertEqual(r['status'],'completed');self.assertGreater(report['target_snapshots'],0)
            self.assertTrue(any(p['target_match']=='executable' for p in report['actual_executables']))
            self.assertTrue(any(p['target_match'] is None for p in report['actual_executables']))

    def test_timeout_and_cancel_cleanup_preserve_observation(self):
        with tempfile.TemporaryDirectory() as d:
            r=self.observe_python(Path(d),code='import time; time.sleep(30)',timeout=.06)
            self.assertEqual(r['status'],'timeout');self.assertTrue((Path(d)/'runtime-identity.json').is_file())
        with tempfile.TemporaryDirectory() as d:
            real_wait=subprocess.Popen.wait;once=[False]
            def interrupt(process,timeout=None):
                if not once[0]:once[0]=True;raise KeyboardInterrupt
                return real_wait(process,timeout)
            with mock.patch.object(subprocess.Popen,'wait',interrupt):r=self.observe_python(Path(d),code='import time; time.sleep(30)')
            self.assertEqual(r['status'],'cancelled');self.assertTrue((Path(d)/'runtime-identity.json').is_file())

    def test_comparison_rejects_missing_identity_evidence_and_observer_control_changes(self):
        a,b=comparison_result('a'),comparison_result('b')
        self.assertTrue(compare(a,b)['comparable']) # Existing records require no runtime evidence.
        for r in (a,b):r['request']['measurement']['runtime_identity']=config()
        with self.assertRaises(LabError):compare(a,b)
        for r in (a,b):
            r['request']['measurement']['repetitions']=7
            r['runtime_identities']=[dict(execution_stage='preflight',measurement_group=None,acceptance=dict(status='NOT_REQUESTED')),
                *[dict(execution_stage='measurement',measurement_group=0,acceptance=dict(status='NOT_REQUESTED')) for _ in range(1)]]
        # Fixture tool is a batch adapter: one launch contains all formal observations.
        self.assertTrue(compare(a,b)['comparable'])
        b['request']['measurement']['runtime_identity']['interval_ms']=20
        with self.assertRaises(LabError):compare(a,b)

    def test_budget_counts_observation_without_extra_launches_and_rejects_absent_preflight(self):
        from cppperflab.budget import execution_budget
        with tempfile.TemporaryDirectory() as d:
            r=request_template(d);r['measurement']['tool']='perf-stat'
            plain=execution_budget(r)
            r['measurement']['runtime_identity']=config()
            observed=execution_budget(r);a=observed['candidates'][0];b=plain['candidates'][0]
            self.assertEqual(a['main_execution_groups'],b['main_execution_groups'])
            self.assertEqual(a['runtime_identity']['additional_target_launches'],0)
            self.assertEqual(a['runtime_identity']['observed_launches'],1+r['measurement']['repetitions'])
            r['measurement'].update(tool='google-benchmark',warmups=0,options={'filter':'test'})
            self.assertFalse(execution_budget(r)['candidates'][0]['eligible'])

    def test_lab_records_actual_reports_and_gate_failure_before_formal_samples(self):
        from cppperflab.lab import run
        from cppperflab.storage import Store
        class Adapter:
            batch=False
            def describe(self):return {'id':'test-runtime','mode':'test identity integration'}
            def prepare(self,command,*args,**kwargs):return command
            def parse(self,folder,options,record):return {'metrics':[],'hotspots':[]}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);r=request_template(root)
            r['target']['argv']=[sys.executable,'-c','import time; time.sleep(.08)']
            r['measurement'].update(tool='test-runtime',repetitions=2,warmups=0,runtime_identity=config())
            plan={'selected':'test-runtime','probes':{'tools':{'test-runtime':{'version':'test'}}}}
            store=Store(root/'store')
            with mock.patch('cppperflab.lab.plan',return_value=plan),mock.patch.dict('cppperflab.lab.REGISTRY',{'test-runtime':Adapter()}):
                result=run(r,store)
                self.assertEqual(result['status'],'completed',result['error'])
                self.assertEqual(len(result['runtime_identities']),3)
                self.assertEqual([i['measurement_group'] for i in result['runtime_identities']],[None,0,1])
                for report in result['runtime_identities']:
                    self.assertEqual(report['evidence_state'],'Measured')
                    self.assertTrue(any(e['path']==report['evidence'] for e in result['evidence']))
                from cppperflab.views import build_view
                from cppperflab.html_report import render_html
                from test_views import SnapshotParser
                view=build_view(store,result['run_id'])
                html=render_html(view,store,root/'identity.html')
                parser=SnapshotParser();parser.feed(html)
                self.assertEqual(json.loads(parser.snapshot),view)
                self.assertIn('实际执行文件与加载映射',html)
                exe=str(Path(sys.executable).resolve())
                r['measurement']['runtime_identity']['required_objects']=[dict(basename=Path(exe).name,path=exe,sha256='0'*64)]
                failed=run(r,store)
                self.assertEqual(failed['status'],'failed')
                self.assertEqual(failed['error']['details']['acceptance']['status'],'FAIL')
                self.assertEqual(failed['samples'],[]);self.assertEqual(failed['correctness']['status'],'passed')
                self.assertEqual(len(failed['runtime_identities']),1)

    def test_process_limit_and_unsupported_requirement_never_imply_pass(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=config();cfg['max_processes']=1
            execute(['/bin/sh','-c','"$1" -c "import time; time.sleep(.1)" & wait','wrapper',sys.executable],Path(d),timeout=3,
                runtime_identity=cfg,identity_target=str(Path(sys.executable).resolve()))
            report=json.loads((Path(d)/'runtime-identity.json').read_text())
            self.assertIn('max_processes',report['truncations']);self.assertEqual(report['evidence_state'],'Missing')
        with tempfile.TemporaryDirectory() as d:
            exe=str(Path(sys.executable).resolve())
            observer=RuntimeObserver(Path(d),config(required_objects=[dict(basename=Path(exe).name,path=exe,sha256=sha256(exe))]),exe)
            observer.errors.append(dict(evidence_state='Unsupported',reason='controlled denied maps'))
            report=observer.finish();self.assertEqual(report['acceptance']['status'],'UNSUPPORTED')

    def test_plan_does_not_claim_low_overhead_for_observed_measurement(self):
        from cppperflab.lab import plan
        from cppperflab.storage import Store
        with tempfile.TemporaryDirectory() as d:
            r=request_template(d);r['measurement'].update(tool='perf-stat',runtime_identity=config())
            r['goal']=dict(metrics=['time.wall'],max_overhead='low',allow_instrumentation=False)
            probe={'tools':{'perf-stat':{'available':True,'version':'controlled fixture'}}}
            with mock.patch('cppperflab.lab.probe_tools',return_value=probe):result=plan(r,Store(Path(d)/'store'))
            self.assertIsNone(result['selected'])
            self.assertIn('runtime_observer_measurement_overhead_is_unquantified',result['candidates'][0]['reasons'])

    def test_each_observed_measurement_group_must_be_present_once(self):
        a,b=comparison_result('a'),comparison_result('b')
        for r in (a,b):
            r['tool']['id']='perf-stat'
            r['request']['measurement'].update(repetitions=7,runtime_identity=config())
            r['runtime_identities']=[dict(execution_stage='preflight',measurement_group=None,acceptance=dict(status='NOT_REQUESTED')),
                *[dict(execution_stage='measurement',measurement_group=i,acceptance=dict(status='NOT_REQUESTED')) for i in range(7)]]
        self.assertTrue(compare(a,b)['comparable'])
        b['runtime_identities'][-1]['measurement_group']=0
        with self.assertRaises(LabError):compare(a,b)
