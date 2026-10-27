"""Native resource adapter uses the existing command and failure contracts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.adapters import REGISTRY
from cppperflab.errors import LabError


def report(exit_code=0, exec_errno=None, signal=None):
    return {'schema_version':1, 'collector':'linux-wait4', 'version':1, 'child_pid':42,
            'wait_status': (exit_code << 8) if signal is None else signal,
            'exited':signal is None, 'exit_code':exit_code if signal is None else None,
            'signal':signal, 'exec_errno':exec_errno,
            'rusage':{'max_rss_kib':0, 'user_time':{'seconds':1,'microseconds':500},
                      'system_time':{'seconds':0,'microseconds':25}, 'minor_faults':10,
                      'major_faults':0, 'voluntary_context_switches':1,
                      'involuntary_context_switches':0, 'filesystem_inputs':0, 'filesystem_outputs':0}}


class NativeResourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = REGISTRY['native-resource']

    def test_registration_and_prepare_preserve_argv(self):
        self.assertFalse(self.adapter.batch)
        self.assertNotIn('time.wall', self.adapter.metrics)
        self.assertEqual(len(self.adapter.metrics), 5)
        self.adapter.validate({})
        with self.assertRaises(LabError): self.adapter.validate({'unknown':True})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(type(self.adapter), 'tool_path', return_value='/local/perflab-resource'):
            directory = Path(tmp)
            self.assertEqual(self.adapter.prepare(['/bin/echo', 'hello world'], directory, {}),
                             ['/local/perflab-resource', '--output', str(directory/'native-resource.json'), '--', '/bin/echo', 'hello world'])

    def test_success_zero_rss_and_raw_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'native-resource.json').write_text(json.dumps(report()))
            result = self.adapter.parse(directory, {}, {'status':'completed', 'returncode':0})
            values = {row['name']:row['value'] for row in result['metrics']}
            self.assertEqual(values['memory.rss_peak'], 0)
            self.assertEqual(values['time.cpu.user'], 1.0005)
            self.assertEqual(values['os.minor_page_faults'], 10)
            self.assertTrue(all(row['evidence']=='native-resource.json' for row in result['metrics']))

    def test_target_failure_exec_failure_and_exit_mismatch(self):
        cases = [(report(7), 7, 'target_failed'),
                 (report(127), 127, 'target_failed'),
                 (report(127, exec_errno=2), 127, 'tool_failed'),
                 (report(signal=15), 143, 'target_failed'),
                 (report(0), 1, 'tool_failed')]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for data, code, category in cases:
                with self.subTest(data=data, code=code):
                    (directory/'native-resource.json').write_text(json.dumps(data))
                    with self.assertRaises(LabError) as caught:
                        self.adapter.parse(directory, {}, {'status':'completed','returncode':code})
                    self.assertEqual(caught.exception.category, category)
                    self.assertEqual(caught.exception.details['child_status']['exec_errno'], data['exec_errno'])
                    self.assertTrue((directory/'native-resource.json').exists())

    def test_missing_empty_malformed_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for content, category in [(None,'tool_failed'), ('','tool_failed'), ('bad json','parse_failed')]:
                with self.subTest(content=content):
                    path = directory/'native-resource.json'
                    if content is not None: path.write_text(content)
                    with self.assertRaises(LabError) as caught:
                        self.adapter.parse(directory, {}, {'status':'completed','returncode':1})
                    self.assertEqual(caught.exception.category, category)
            with self.assertRaises(LabError) as caught:
                self.adapter.parse(directory, {}, {'status':'timeout','returncode':-15})
            self.assertEqual(caught.exception.category, 'timeout')

    def test_probe_missing_and_wrong_version_never_claim_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with mock.patch.object(type(self.adapter), 'tool_path', return_value=None):
                missing = self.adapter.probe(base/'missing')
            self.assertFalse(missing['available'])
            self.assertEqual(missing['reason'], 'executable_missing')
            def fake_execute(argv, directory, **kwargs):
                directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
                (directory/'stdout.txt').write_text('unrelated tool 1\n')
                (directory/'stderr.txt').write_text('')
                return {'status':'completed','returncode':0}
            with mock.patch.object(type(self.adapter), 'tool_path', return_value='/fake/tool'), \
                 mock.patch('cppperflab.adapters.execute', side_effect=fake_execute):
                mismatch = self.adapter.probe(base/'mismatch')
            self.assertFalse(mismatch['available'])
            self.assertEqual(mismatch['reason'], 'version_mismatch')

    def test_probe_valid_version_requires_actual_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); fake = base/'fake-helper'; fake.write_text('fixture')
            def fake_execute(argv, directory, **kwargs):
                directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
                (directory/'stdout.txt').write_text('CppPerfLab native-resource 1\n' if directory.name=='version' else '')
                (directory/'stderr.txt').write_text('')
                if directory.name=='execution': (directory/'native-resource.json').write_text(json.dumps(report()))
                return {'status':'completed','returncode':0}
            with mock.patch.object(type(self.adapter), 'tool_path', return_value=str(fake)), \
                 mock.patch('cppperflab.adapters.execute', side_effect=fake_execute):
                result = self.adapter.probe(base/'probe')
            self.assertTrue(result['available'])
            self.assertIn('memory.rss_peak', result['metrics'])
            self.assertTrue((base/'probe/execution/native-resource.json').exists())


if __name__ == '__main__':
    unittest.main()
