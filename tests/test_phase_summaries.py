import copy
import unittest

from cppperflab.errors import LabError
from cppperflab.phase_summaries import normalize_summary


def report():
    return {
        'schema_version': 1, 'representation': 'summary', 'scope': 'solver',
        'source': {'input_sha256': 'a' * 64, 'mapping_sha256': 'b' * 64,
                   'input_lines': 5, 'matched_lines': 5, 'ignored_lines': 0,
                   'source_path': 'synthetic.log', 'mapping_path': 'synthetic-map.json'},
        'stages': [
            {'id': 'solve', 'name': 'Solve', 'parent_id': None,
             'inclusive': {'seconds': 2.0, 'evidence_state': 'Measured'},
             'self': {'seconds': 1.25, 'evidence_state': 'Measured'}, 'calls': 1,
             'correctness': 'passed', 'children_coverage': 'unknown',
             'work': {'iterations': {'value': 10, 'unit': 'count', 'evidence_state': 'Measured'}},
             'source_lines': {'count': 4, 'first': 10, 'last': 13}},
            {'id': 'factor', 'name': 'Factor', 'parent_id': 'solve',
             'inclusive': {'seconds': 0.3, 'evidence_state': 'Estimated'},
             'self': None, 'calls': None, 'correctness': 'not_checked',
             'children_coverage': 'complete_nonoverlapping', 'work': {},
             'source_lines': {'count': 0, 'first': None, 'last': None}},
            {'id': 'pivot', 'name': 'Pivot', 'parent_id': 'factor',
             'inclusive': {'seconds': 0.1, 'evidence_state': 'Measured'},
             'self': None, 'calls': None, 'correctness': 'not_checked',
             'children_coverage': 'unknown', 'work': {},
             'source_lines': {'count': 0, 'first': None, 'last': None}},
            {'id': 'backsub', 'name': 'Backsub', 'parent_id': 'factor',
             'inclusive': {'seconds': 0.2, 'evidence_state': 'Measured'},
             'self': None, 'calls': None, 'correctness': 'not_checked',
             'children_coverage': 'unknown', 'work': {},
             'source_lines': {'count': 0, 'first': None, 'last': None}},
        ],
    }


class PhaseSummaryTests(unittest.TestCase):
    def test_reported_and_derived_self_preserve_evidence_states(self):
        result = normalize_summary(report(), '/tmp/run.summary.json')
        by_id = {row['id']: row for row in result['phase_summaries']}
        parent, child = by_id['solve'], by_id['factor']
        self.assertEqual(parent['self_method'], 'reported')
        self.assertEqual(parent['self_evidence_state'], 'Measured')
        self.assertEqual(child['self_method'], 'estimated_complete_children')
        self.assertEqual(child['self_seconds'], 0.0)
        self.assertEqual(child['self_evidence_state'], 'Estimated')
        self.assertEqual(child['path'], ['solve', 'factor'])
        self.assertTrue(any(m['name'] == 'phase.inclusive_time' and m['evidence_state'] == 'Estimated'
                            for m in result['metrics']))

    def test_missing_values_are_not_zero_and_no_fake_timeline(self):
        data = report()
        data['stages'][0]['inclusive'] = None
        data['stages'][0]['self'] = None
        data['stages'][0]['calls'] = None
        result = normalize_summary(data, '/tmp/run.summary.json')
        row = result['phase_summaries'][0]
        self.assertIsNone(row['inclusive_seconds'])
        self.assertIsNone(row['self_seconds'])
        self.assertIsNone(row['calls'])
        self.assertEqual(result['phases'], [])
        self.assertIsNone(result['clock'])
        self.assertEqual(result['thread_timeline']['evidence_state'], 'Missing')
        self.assertTrue(all(k not in row for k in ('start_ns', 'end_ns')))
        self.assertTrue(all(m['evidence_state'] == 'Missing'
                            for m in result['unavailable_observations']
                            if m['name'] in ('phase.root_wall', 'phase.covered_wall', 'phase.uncovered_wall')))

    def test_invalid_hierarchy_and_false_complete_coverage_are_rejected(self):
        for mutation in ('parent', 'cycle', 'overlap_declaration'):
            data = copy.deepcopy(report())
            if mutation == 'parent':
                data['stages'][1]['parent_id'] = 'absent'
            elif mutation == 'cycle':
                data['stages'][0]['parent_id'] = 'factor'
            else:
                data['stages'][0]['self'] = None
                data['stages'][0]['children_coverage'] = 'complete_nonoverlapping'
                data['stages'][1]['inclusive']['seconds'] = 2.5
            with self.subTest(mutation=mutation), self.assertRaises(LabError) as caught:
                normalize_summary(data, 'bad.json')
            self.assertEqual(caught.exception.category, 'parse_failed')

    def test_counter_contract_changes_by_semantics_not_value(self):
        first = normalize_summary(report(), 'one.json')
        changed = report()
        changed['stages'][0]['inclusive']['seconds'] = 99
        changed['stages'][0]['source_lines'] = {'count': 20, 'first': 2, 'last': 21}
        changed['stages'][0]['children_coverage'] = 'complete_nonoverlapping'
        second = normalize_summary(changed, 'two.json')
        a = next(m for m in first['metrics'] if m['name'] == 'phase.inclusive_time')
        b = next(m for m in second['metrics'] if m['name'] == 'phase.inclusive_time')
        self.assertEqual(a['counter_contract'], b['counter_contract'])
        self.assertNotEqual(a['value'], b['value'])
        self.assertNotEqual(next(m for m in first['metrics'] if m['name'] == 'phase.self_time')['counter_contract'],
                            next(m for m in second['metrics'] if m['name'] == 'phase.self_time')['counter_contract'])
        self.assertEqual(first['source'], report()['source'])


if __name__ == '__main__':
    unittest.main()
