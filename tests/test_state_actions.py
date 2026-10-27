"""Execution-state evidence and budget at the existing process boundary."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from cppperflab.budget import execution_budget
from cppperflab.errors import LabError
from cppperflab.execution import check_execution, execute
from cppperflab.state import cleanup_state, execute_state, state_actions
from cppperflab.storage import write_json
from test_core import request_template


def command(script, *args):
    return {'argv': [sys.executable, '-c', script, *map(str, args)]}


def launcher(argv, folder, *, execution_stage):
    folder.mkdir(parents=True, exist_ok=True)
    record = execute(argv, folder, timeout=10)
    write_json(folder / 'execution.json', record)
    check_execution(record)
    if record['returncode']:
        raise LabError('target_failed', 'Consumer state action failed',
                       {'returncode': record['returncode'], 'stage': execution_stage})
    return record


def request(root):
    result = request_template(root)
    result['measurement'].update(tool='perf-stat', repetitions=4, warmups=2, timeout_seconds=30)
    result['execution_state'] = {'schema_version': 1, 'scope': 'measurement_sample',
        'process': 'fresh_process', 'object_state': 'consumer-owned object state',
        'cache': 'uncontrolled', 'external_state': 'consumer-owned file', 'required': True}
    return result


class StateActionsTests(unittest.TestCase):
    def test_real_actions_execute_in_order_and_archive_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / 'external.txt'
            r = request(root)
            state = r['execution_state']
            state['setup'] = command("from pathlib import Path; import sys; Path(sys.argv[1]).write_text('setup')", marker)
            state['reset'] = command("from pathlib import Path; import sys; p=Path(sys.argv[1]); assert p.read_text()=='setup'; p.write_text('ready')", marker)
            state['verify'] = command("from pathlib import Path; import json,sys; print(json.dumps({'schema_version':1,'status':'PASS' if Path(sys.argv[1]).read_text()=='ready' else 'FAIL','description':'checks the consumer file','observations':{'observed':'ready'}}))", marker)
            state['cleanup'] = command("from pathlib import Path; import sys; Path(sys.argv[1]).write_text('cleaned')", marker)
            directory = root / 'execution-state-000'
            result = execute_state(r, directory, launcher)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual([item['name'] for item in result['actions']], ['setup', 'reset', 'verify'])
            self.assertEqual(result['verification']['observations'], {'observed': 'ready'})
            self.assertIn('execution-state-000/verify/stdout.txt', result['evidence_refs'])
            self.assertTrue(all((root / path).is_file() for path in result['evidence_refs']))
            cleanup = cleanup_state(r, directory, launcher)
            self.assertEqual(cleanup['status'], 'PASS')
            self.assertEqual(marker.read_text(), 'cleaned')
            self.assertIn('execution-state-000/cleanup/execution.json', cleanup['evidence_refs'])

    def test_successful_reset_without_verifier_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            r = request(root)
            r['execution_state']['reset'] = command("print('reset action executed')")
            result = execute_state(r, root / 'execution-state-000', launcher)
            self.assertEqual(result['actions'][0]['status'], 'PASS')
            self.assertEqual(result['status'], 'INCONCLUSIVE')
            self.assertIsNone(result['verification'])

    def test_failing_action_stops_and_preserves_native_error_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / 'unexpected'
            r = request(root)
            r['execution_state']['setup'] = command('import sys; sys.exit(9)')
            r['execution_state']['reset'] = command("from pathlib import Path; import sys; Path(sys.argv[1]).touch()", marker)
            with self.assertRaises(LabError) as caught:
                execute_state(r, root / 'execution-state-000', launcher)
            self.assertEqual(caught.exception.category, 'target_failed')
            evidence = caught.exception.details['execution_state_evidence']
            self.assertEqual(evidence['status'], 'FAIL')
            self.assertEqual([item['name'] for item in evidence['actions']], ['setup'])
            self.assertEqual(evidence['actions'][0]['execution']['returncode'], 9)
            self.assertIn('execution-state-000/setup/execution.json', evidence['evidence_refs'])
            self.assertFalse(marker.exists())

    def test_verifier_failure_and_inconclusive_stop_formal_execution(self):
        for status in ('FAIL', 'INCONCLUSIVE'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                r = request(root)
                r['execution_state']['verify'] = command(
                    "import json,sys; print(json.dumps({'schema_version':1,'status':sys.argv[1],'description':'state check'}))", status)
                with self.assertRaises(LabError) as caught:
                    execute_state(r, root / 'execution-state-000', launcher)
                self.assertEqual(caught.exception.category, 'incomparable')
                evidence = caught.exception.details['execution_state_evidence']
                self.assertEqual(evidence['status'], status)
                self.assertEqual(evidence['verification']['status'], status)
                self.assertIn('execution-state-000/verify/stdout.txt', evidence['evidence_refs'])

    def test_malformed_verifier_output_is_invalid_not_a_pass(self):
        for output in ('not-json', '{"schema_version":1,"status":"PASS","description":"ok","extra":1}',
                       '{"schema_version":1,"status":"PASS","description":"ok","observations":{"x":1e999}}'):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                r = request(root)
                r['execution_state']['verify'] = command('import sys; print(sys.argv[1])', output)
                with self.assertRaises(LabError) as caught:
                    execute_state(r, root / 'execution-state-000', launcher)
                self.assertEqual(caught.exception.category, 'parse_failed')
                evidence = caught.exception.details['execution_state_evidence']
                self.assertEqual(evidence['status'], 'INCONCLUSIVE')
                self.assertIsNone(evidence['verification'])
                self.assertIn('execution-state-000/verify/stdout.txt', evidence['evidence_refs'])

    def test_cleanup_failure_is_visible_to_caller(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            r = request(root)
            r['execution_state']['cleanup'] = command('import sys; sys.exit(4)')
            directory = root / 'execution-state-000'
            directory.mkdir()
            with self.assertRaises(LabError) as caught:
                cleanup_state(r, directory, launcher)
            self.assertEqual(caught.exception.category, 'target_failed')
            cleanup = caught.exception.details['execution_state_cleanup']
            self.assertEqual(cleanup['status'], 'FAIL')
            self.assertEqual(cleanup['action']['execution']['returncode'], 4)

    def test_budget_includes_each_action_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            r = request(Path(temp))
            for name in ('setup', 'reset', 'verify', 'cleanup'):
                r['execution_state'][name] = command('pass')
            with mock.patch('cppperflab.execution.execute', side_effect=AssertionError('executed')):
                budget = execution_budget(r)
            candidate = budget['candidates'][0]
            self.assertFalse(budget['execution_performed'])
            self.assertEqual(candidate['execution_state']['action_commands'],
                             {'setup': 4, 'reset': 4, 'verify': 4, 'cleanup': 4})
            self.assertEqual(candidate['execution_state']['additional_commands'], 16)
            self.assertIsNone(candidate['execution_state']['additional_target_computation_runs'])
            self.assertEqual(candidate['main_execution_groups'], 24)
            self.assertEqual(candidate['configured_timeout_budget_seconds'], 720)

    def test_batch_invocation_is_supported_but_batch_sample_scope_is_not(self):
        with tempfile.TemporaryDirectory() as temp:
            r = request(Path(temp))
            r['measurement']['tool'] = 'hyperfine'
            r['execution_state']['verify'] = command('pass')
            candidate = execution_budget(r)['candidates'][0]
            self.assertFalse(candidate['eligible'])
            self.assertEqual(candidate['reason']['category'], 'unavailable')
            r['execution_state']['scope'] = 'measurement_invocation'
            candidate = execution_budget(r)['candidates'][0]
            self.assertTrue(candidate['eligible'])
            self.assertEqual(candidate['execution_state']['controlled_invocations'], 1)
            self.assertEqual(candidate['execution_state']['action_commands']['verify'], 1)

    def test_legacy_request_needs_no_state_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            r = request(Path(temp))
            del r['execution_state']
            self.assertEqual(state_actions(r), [])
            self.assertIsNone(execution_budget(r)['candidates'][0]['execution_state'])


if __name__ == '__main__':
    unittest.main()
