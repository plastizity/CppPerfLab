"""Lifecycle observations retain honest process, progress and candidate status."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from cppperflab.budget import execution_budget
from cppperflab.cli import dispatch, parser
from cppperflab.errors import LabError
from cppperflab.execution import execute
from cppperflab.lifecycle import RunProgress, StudyProgress, observe_process, process_identity, run_status, status_from_result
from cppperflab.protocol import sha256
from cppperflab.storage import Store, write_json
from test_core import request_template


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'store')

    def tearDown(self):
        self.temp.cleanup()

    def test_progress_option_budget_and_overhead_contract(self):
        from cppperflab.lab import plan
        from cppperflab.protocol import load_request
        request=request_template(self.root)
        request['measurement'].update(tool='perf-stat',progress=True)
        request['goal']=dict(metrics=['time.wall'],max_overhead='low',allow_instrumentation=False)
        config=self.root/'request.json';write_json(config,request)
        loaded=load_request(config)
        budget=execution_budget(loaded)['candidates'][0]
        self.assertEqual(budget['progress']['additional_target_launches'],0)
        self.assertEqual(budget['known_target_computation_runs'],4)
        with mock.patch('cppperflab.lab.probe_tools',return_value=dict(tools={'perf-stat':dict(available=True)})):
            selected=plan(loaded,self.store)
        self.assertIsNone(selected['selected'])
        self.assertIn('live_progress_metadata_overhead_is_unquantified',selected['candidates'][0]['reasons'])
        request['measurement']['progress']='yes';write_json(config,request)
        with self.assertRaises(LabError):load_request(config)

    def test_study_progress_registers_and_finishes_multiple_children(self):
        directory = self.root / 'study'; directory.mkdir()
        result = {'status': 'running'}
        progress = StudyProgress(result, directory, [{'measurement': {'progress': True}}], 2)
        first = progress.register_child('run', 'run-a', {'slot': 0})
        second = progress.register_child('run', 'run-b', {'slot': 1})
        self.assertEqual((first, second), (0, 1))
        self.assertEqual(progress.data['active_children'], [0, 1])
        self.assertIsNone(progress.data['active_child'])
        progress.finish_child(first)
        self.assertEqual(progress.data['active_children'], [1])
        self.assertEqual(progress.data['active_child'], second)
        progress.terminal()
        self.assertEqual(progress.data['active_children'], [])
        self.assertIsNone(progress.data['active_child'])

    def test_disabled_study_progress_does_not_register_or_write(self):
        directory = self.root / 'study'; directory.mkdir()
        progress = StudyProgress({'status': 'running'}, directory,
            [{'measurement': {'progress': False}}], 1)
        self.assertIsNone(progress.register_child('run', 'run-a', {}))
        self.assertIsNone(progress.finish_child(0))
        self.assertFalse((directory / 'study.json').exists())

    def test_early_run_without_fingerprints_cannot_claim_artifacts_verified(self):
        identifier,_,_=self.record(status='running')
        status=run_status(self.store,identifier,verify_artifacts=True)
        self.assertFalse(status['artifacts_verified'])
        self.assertEqual(status['artifact_verification']['evidence_state'],'Missing')
        self.assertEqual(status['current_artifacts'],[])

    def test_live_progress_is_a_comparison_control_and_legacy_false_is_compatible(self):
        from cppperflab.comparison import compare
        from test_core import comparison_result
        baseline=comparison_result('baseline');candidate=comparison_result('candidate')
        candidate['request']['measurement']['progress']=False
        self.assertTrue(compare(baseline,candidate)['comparable'])
        candidate['request']['measurement']['progress']=True
        for allow in (False,True):
            with self.assertRaises(LabError) as caught:compare(baseline,candidate,allow_command_change=allow)
            self.assertEqual(caught.exception.category,'incomparable')
            self.assertIn('measurement.progress',str(caught.exception.details))

    def test_start_callback_time_consumes_the_existing_process_timeout(self):
        real_wait=subprocess.Popen.wait;timeouts=[]
        def wait(process,timeout=None):
            timeouts.append(timeout);return real_wait(process,timeout=timeout)
        with mock.patch('cppperflab.execution.time.monotonic',side_effect=[0,0,2,3]),mock.patch.object(subprocess.Popen,'wait',wait):
            record=execute([sys.executable,'-c','import time; time.sleep(30)'],self.root/'deadline',timeout=1,on_start=lambda pid:None)
        self.assertEqual(timeouts[0],0)
        self.assertEqual(record['status'],'timeout')
        self.assertIsInstance(record['pid'],int)

    def record(self, *, status='completed', started='2026-10-26T00:00:00+00:00',
               correctness='passed', error=None, identity=None, lifecycle=None):
        run_id, directory = self.store.create()
        result = dict(schema_version=1, run_id=run_id, status=status, started_at=started,
            ended_at=None if status == 'running' else started, request=dict(name='synthetic lifecycle fixture',
                target=dict(build=dict(revision='synthetic', compiler='unknown', flags=[], mode='test', artifacts=[]))),
            correctness=dict(status=correctness), error=error, identity=identity or {'artifacts': {}, 'inputs': {}},
            samples=[], evidence=[])
        if lifecycle is not None:
            result['lifecycle'] = lifecycle
        write_json(directory / 'result.json', result)
        return run_id, result, directory

    def test_actual_process_identity_detects_pid_reuse_exit_and_namespace_mismatch(self):
        identity = process_identity(os.getpid())
        self.assertEqual(identity['evidence_state'], 'Measured')
        self.assertTrue(observe_process(identity)['alive'])

        with mock.patch('cppperflab.lifecycle.process_identity', return_value=dict(identity, start_ticks=identity['start_ticks'] + 1)):
            reused = observe_process(identity)
        self.assertFalse(reused['alive'])
        self.assertEqual(reused['state'], 'pid_reused')

        with mock.patch('cppperflab.lifecycle.process_identity', return_value=dict(pid=identity['pid'], evidence_state='Missing', reason='gone')):
            exited = observe_process(identity)
        self.assertFalse(exited['alive'])
        self.assertEqual(exited['state'], 'exited')

        wrong_namespace = dict(identity, pid_namespace='pid:[different]')
        other = observe_process(wrong_namespace)
        self.assertIsNone(other['alive'])
        self.assertEqual(other['state'], 'different_context')

    def test_legacy_running_record_without_owner_is_unverified(self):
        run_id, result, _ = self.record(status='running')
        current = status_from_result(self.store, result)
        self.assertEqual(current['run_status'], 'running')
        self.assertEqual(current['observed_state'], 'unverified')
        self.assertEqual(current['owner']['evidence_state'], 'Missing')
        self.assertIsNone(current['error'])
        self.assertIsNone(run_status(self.store, run_id)['owner']['alive'])

    def test_verify_artifacts_reports_missing_and_changed_without_repair(self):
        changed = self.root / 'changed.bin';changed.write_bytes(b'current bytes')
        missing = self.root / 'removed.bin'
        declared = {'artifacts': {str(changed): '0' * 64, str(missing): '1' * 64}, 'inputs': {}}
        run_id, result, _ = self.record(identity=declared)
        original_result_hash = sha256(self.store.record_path(run_id))
        before = changed.read_bytes()
        status = run_status(self.store, run_id, verify_artifacts=True)
        rows = {row['path']: row for row in status['current_artifacts']}
        self.assertEqual(status['observed_state'],'terminal')
        self.assertFalse(status['artifacts_verified'])
        self.assertEqual(rows[str(changed)]['evidence_state'], 'Invalid')
        self.assertFalse(rows[str(changed)]['matches'])
        self.assertEqual(rows[str(missing)]['evidence_state'], 'Missing')
        self.assertIsNone(rows[str(missing)]['matches'])
        self.assertEqual(changed.read_bytes(), before)
        self.assertFalse(missing.exists())
        self.assertEqual(sha256(self.store.record_path(run_id)), original_result_hash)

    def test_progress_uses_real_budget_counts_and_eta_only_after_two_groups(self):
        request = request_template(self.root)
        request['measurement'].update(tool='perf-stat', options={'events': ['duration_time']}, progress=True,
                                      repetitions=4, warmups=1)
        budget = execution_budget(request)
        candidate = budget['candidates'][0]
        self.assertEqual(candidate['stages'][4]['stage'], 'measurement')
        self.assertEqual(candidate['stages'][4]['commands'], 4)
        run_id, result, directory = self.record(status='running')
        result['request'] = request
        progress = RunProgress(result, directory)
        progress.configure(candidate)
        self.assertEqual(progress.data['measurement_groups']['planned'], 4)
        self.assertIsNone(progress.data['eta']['total_remaining_seconds'])
        progress.group_finished(10.0)
        self.assertEqual(progress.data['measurement_groups']['completed'], 1)
        self.assertEqual(progress.data['eta']['evidence_state'], 'Missing')
        progress.group_finished(20.0)
        self.assertEqual(progress.data['measurement_groups']['completed'], 2)
        self.assertEqual(progress.data['eta']['evidence_state'], 'Estimated')
        self.assertEqual(progress.data['eta']['measurement_groups_remaining'], 2)
        self.assertEqual(progress.data['eta']['measurement_stage_remaining_seconds'], 30.0)
        self.assertIsNone(progress.data['eta']['total_remaining_seconds'])
        result['status'] = 'completed'
        progress.terminal()
        progress.save()
        self.assertIsNone(progress.data['active_process'])
        self.assertEqual(progress.data['eta']['evidence_state'], 'Missing')
        self.assertIsNone(progress.data['eta']['total_remaining_seconds'])

    def test_progress_published_while_process_runs_then_cleared_at_exit(self):
        request = request_template(self.root)
        request['measurement'].update(tool='perf-stat', options={'events': ['duration_time']}, progress=True,
                                      repetitions=2, warmups=0)
        candidate = execution_budget(request)['candidates'][0]
        run_id, result, directory = self.record(status='running')
        result['request'] = request
        progress = RunProgress(result, directory)
        progress.configure(candidate)
        ready = self.root / 'child-ready'
        command = [sys.executable, '-c', 'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text("ready"); print("ready",flush=True); time.sleep(.4)', str(ready)]

        def launch():
            record = execute(command, directory / 'measurement-000', timeout=3,
                on_start=lambda pid: progress.starting('measurement', 0, command, directory / 'measurement-000', pid))
            write_json(directory / 'measurement-000' / 'execution.json', record)
            progress.finished('measurement')
            return record

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(launch)
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(ready.exists())
            observed = run_status(self.store, run_id)
            self.assertEqual(observed['observed_state'], 'running_confirmed')
            self.assertEqual(observed['active_process']['observation']['state'], 'alive')
            self.assertEqual(observed['active_process']['execution_stage'], 'measurement')
            self.assertEqual(observed['progress']['measurement_groups']['completed'], 0)
            record = future.result(timeout=3)
        self.assertEqual(record['status'], 'completed')
        self.assertIsNone(run_status(self.store, run_id)['active_process'])

    def test_callback_errors_cleanup_process_group_and_preserve_logs(self):
        for interruption in ('oserror', 'keyboardinterrupt'):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as d:
                root = Path(d);ready = root / 'ready.json';directory = root / 'execution'
                code = ('import json,pathlib,subprocess,sys,time; '
                        'print("child stdout retained",flush=True); '
                        'print("child stderr retained",file=sys.stderr,flush=True); '
                        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                        'pathlib.Path(sys.argv[1]).write_text(json.dumps({"child":p.pid})); time.sleep(30)')
                child_pid = {}
                def on_start(pid):
                    child_pid['parent'] = pid
                    deadline = time.monotonic() + 3
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.005)
                    if not ready.exists():
                        raise AssertionError('target did not publish child PID')
                    child_pid['descendant'] = json.loads(ready.read_text())['child']
                    if interruption == 'oserror':
                        raise OSError('progress persistence failed')
                    raise KeyboardInterrupt
                if interruption == 'oserror':
                    with self.assertRaisesRegex(OSError, 'progress persistence failed'):
                        execute([sys.executable, '-c', code, str(ready)], directory, timeout=5, on_start=on_start)
                else:
                    record = execute([sys.executable, '-c', code, str(ready)], directory, timeout=5, on_start=on_start)
                    self.assertEqual(record['status'], 'cancelled')
                self.assertIn('child stdout retained', (directory / 'stdout.txt').read_text())
                self.assertIn('child stderr retained', (directory / 'stderr.txt').read_text())
                for key in ('parent', 'descendant'):
                    pid = child_pid[key]
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        identity = process_identity(pid)
                        if identity['evidence_state'] == 'Missing' or identity.get('process_state') == 'Z':
                            break
                        time.sleep(.01)
                    else:
                        self.fail('owned process group member survived callback failure')

    def test_cli_status_annotate_history_preserve_source_failure(self):
        source_error = dict(category='target_failed', message='synthetic failure', details={}, exit_code=5)
        failed, _, _ = self.record(status='failed', error=source_error, correctness='not_run')
        replacement, _, _ = self.record(started='2026-10-26T00:01:00+00:00')

        args = parser().parse_args(['status', failed, '--store', str(self.store.root)])
        status = dispatch(args)
        self.assertEqual(status['run_status'], 'failed')
        self.assertEqual(status['source_error'], source_error)
        self.assertIsNone(status['error'])
        self.assertEqual(status['observed_state'], 'terminal')

        args = parser().parse_args(['annotate', failed, '--candidate-state', 'retained', '--reason', 'reviewed',
                                   '--expect-revision', '0', '--store', str(self.store.root)])
        annotated = dispatch(args)
        self.assertEqual(annotated['candidate_state'], 'retained')
        args = parser().parse_args(['annotate', failed, '--superseded-by', replacement, '--reason', 'new validated run',
                                   '--expect-revision', '1', '--store', str(self.store.root)])
        annotated = dispatch(args)
        self.assertEqual(annotated['superseded_by'], replacement)

        args = parser().parse_args(['history', '--store', str(self.store.root)])
        history = dispatch(args)
        row = next(item for item in history['runs'] if item['run_id'] == failed)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['candidate']['candidate_state'], 'retained')
        self.assertEqual(row['candidate']['superseded_by'], replacement)
        self.assertEqual(self.store.read(failed)['error'], source_error)


if __name__ == '__main__':
    unittest.main()
