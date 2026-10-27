"""perf strong-group requests preserve actual collection limitations."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.adapters import REGISTRY, parse_stat
from cppperflab.errors import LabError


class PerfGroupTests(unittest.TestCase):
    def setUp(self):
        self.adapter = REGISTRY['perf-stat']
        self.options = {'events': ['duration_time', 'cycles', 'instructions', 'branches'],
                        'event_groups': [['cycles', 'instructions']]}

    def test_group_validation_rejects_invalid_options(self):
        invalid = [[], [['cycles']], [['cycles', 'cycles']], [['cycles', 'duration_time']],
                   [['cycles', 'instructions:u']], [['cycles', 'cache-misses']],
                   [['cycles', 'instructions'], ['cycles', 'branches']],
                   '{cycles,instructions}', [['cycles', 'instructions', 'branches', []]]]
        for groups in invalid:
            with self.subTest(groups=groups), self.assertRaises(LabError):
                self.adapter.validate({**self.options, 'event_groups': groups})
        self.adapter.validate(self.options)

    def test_prepare_strong_group_and_collection_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(type(self.adapter), 'tool_path', return_value='/usr/bin/perf'):
            directory = Path(tmp)
            args = self.adapter.prepare(['/bin/true'], directory, self.options)
            self.assertIn('--no-scale', args)
            self.assertIn('--no-big-num', args)
            self.assertEqual(args[args.index('-e') + 1], 'duration_time,{cycles,instructions},branches')
            descriptor = json.loads((directory / 'perf-collection.json').read_text())
            self.assertEqual(descriptor['groups'], [['cycles', 'instructions']])
            self.assertEqual(descriptor['events'], self.options['events'])
            self.assertEqual(descriptor['capture_scope'], 'target_process_and_inherited_threads')
            self.assertEqual(descriptor['capture_window'], 'whole-target-invocation')
            self.assertEqual(descriptor['counter_scaling'], 'none')
            self.assertTrue(descriptor['strong_grouping_requested'])

    def parse(self, content, events=('cycles', 'instructions'), groups=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'perf.csv'
            path.write_text(content)
            return parse_stat(path, events, groups)

    def test_complete_group_has_evidence_and_unscaled_raw(self):
        parsed = self.parse('2000;;cycles;500;99.00\n1000;;instructions;500;100.00\n',
                            groups=[['cycles', 'instructions']])
        cycles, instructions = parsed['metrics']
        self.assertEqual(cycles['event_group'], 'group-0')
        self.assertEqual(cycles['group_events'], ['cycles', 'instructions'])
        self.assertEqual(instructions['event_group_status'], 'PASS')
        self.assertEqual(cycles['raw_count'], 2000)
        self.assertEqual(cycles['raw_unit'], '')
        self.assertEqual(cycles['raw_event'], 'cycles')
        self.assertEqual(cycles['counter_runtime_ns'], 500)
        self.assertEqual(cycles['counter_scaling'], 'none')
        self.assertEqual(cycles['capture_window'], 'whole-target-invocation')
        self.assertEqual(cycles['collection_evidence'], 'perf-collection.json')

    def test_group_coverage_and_missing_member_remain_inconclusive(self):
        for second in ('1000;;instructions;500;98.99', '1000;;instructions;0;100',
                       '1000;;instructions;499;100',
                       '1000;;instructions:u;500;100',
                       '<not counted>;;instructions;0;0'):
            with self.subTest(second=second):
                parsed = self.parse('2000;;cycles;500;100\n' + second + '\n',
                                    groups=[['cycles', 'instructions']])
                self.assertEqual(parsed['metrics'][0]['event_group_status'], 'INCONCLUSIVE')
                if second.startswith('<'):
                    self.assertEqual(parsed['unavailable_observations'][0]['evidence_state'], 'Missing')
        parsed = self.parse('2000;;cycles;500;100\n<not supported>;;instructions;0;0\n',
                            groups=[['cycles', 'instructions']])
        self.assertEqual(parsed['metrics'][0]['event_group_status'], 'UNSUPPORTED')
        self.assertEqual(parsed['unavailable_observations'][0]['evidence_state'], 'Unsupported')

    def test_wall_duration_has_capture_window_without_changing_scope(self):
        parsed = self.parse('1000;;duration_time;500;100\n', events=('duration_time',))
        item = parsed['metrics'][0]
        self.assertEqual(item['capture_scope'], 'target_process_and_inherited_threads')
        self.assertEqual(item['capture_window'], 'whole-target-invocation')
        self.assertEqual(item['scope'], 'target-command elapsed including exec/startup')
        self.assertEqual(item['method'], 'elapsed-count')

    def test_consistent_perf_modifiers_preserved(self):
        parsed = self.parse('2000;;cycles:u;500;100\n1000;;instructions:u;500;100\n',
                            groups=[['cycles', 'instructions']])
        cycles, instructions = parsed['metrics']
        self.assertEqual(cycles['raw_event'], 'cycles:u')
        self.assertEqual(instructions['event'], 'instructions:u')
        self.assertEqual(cycles['group_events'], ['cycles', 'instructions'])
        self.assertEqual(cycles['event_group_status'], 'PASS')
        self.assertEqual(instructions['event_group_status'], 'PASS')

    def test_invalid_runtime_rejected(self):
        for row in ('1;;cycles;nan;100', '1;;cycles;-1;100', '1;;cycles;3;nan',
                    'nan;;cycles;3;100'):
            with self.subTest(row=row), self.assertRaises(LabError):
                self.parse(row + '\n1;;instructions;3;100\n', groups=[['cycles', 'instructions']])

    def test_probe_rejects_incomplete_group(self):
        def execute(args, directory, **kwargs):
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'stdout.txt').write_text('perf version')
            (directory / 'stderr.txt').write_text('')
            return {'returncode': 0}
        parsed = {'metrics': [{'event':'cycles', 'event_group':'group-0',
                               'event_group_status':'INCONCLUSIVE', 'running_percent':98}],
                  'hotspots': [], 'unavailable_metrics': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(type(self.adapter), 'tool_path', return_value='/usr/bin/perf'), \
             mock.patch.object(type(self.adapter), 'prepare', return_value=['perf']), \
             mock.patch.object(type(self.adapter), 'parse', return_value=parsed), \
             mock.patch('cppperflab.adapters.execute', side_effect=execute):
            result = self.adapter.probe(Path(tmp) / 'probe', self.options)
        self.assertFalse(result['available'])
        self.assertIn('insufficient running coverage', result['diagnostic'])


if __name__ == '__main__':
    unittest.main()
