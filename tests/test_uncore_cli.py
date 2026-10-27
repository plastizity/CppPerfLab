"""CLI routing and discovery status are distinct from measurement success."""
import io
import json
import tempfile
import unittest
from unittest import mock

from cppperflab.cli import main


class UncoreCliTests(unittest.TestCase):
    def call(self,args):
        with tempfile.TemporaryDirectory() as store:
            output=io.StringIO()
            with mock.patch('sys.argv',['perflab',*args,'--store',store,'--json']),mock.patch('sys.stdout',output):
                code=main()
            return code,json.loads(output.getvalue())

    def test_metadata_discovery_does_not_invoke_tool_probes(self):
        result={'schema_version':1,'probe_id':'fixture','capabilities':{'memory_controller_traffic':{
            'execution_performed':False,'collection_available':False,
            'measurement':{'evidence_state':'Unsupported','value':None,'reason':'collector_not_implemented'}}}}
        with mock.patch('cppperflab.uncore.probe',return_value=result) as discovery, \
             mock.patch('cppperflab.cli.probe_tools',side_effect=AssertionError('Executed a tool')):
            code,data=self.call(['tools','probe','--capability','memory-controller-traffic'])
        self.assertEqual(code,0);self.assertTrue(data['ok']);discovery.assert_called_once()
        self.assertFalse(data['result']['capabilities']['memory_controller_traffic']['collection_available'])

    def test_capability_cannot_silently_mix_with_measurement_options(self):
        for tail in (['--tool','perf-stat'],['--events','instructions'],['--event-group','cycles,instructions']):
            with mock.patch('cppperflab.uncore.probe',side_effect=AssertionError('Discovery should not run')):
                code,data=self.call(['tools','probe','--capability','memory-controller-traffic',*tail])
            self.assertEqual(code,2);self.assertEqual(data['error']['category'],'invalid_request')
        code,data=self.call(['tools','list','--capability','memory-controller-traffic'])
        self.assertEqual(code,2)

    def test_tool_list_describes_discovery_without_registering_fake_adapter(self):
        code,data=self.call(['tools','list'])
        self.assertEqual(code,0)
        self.assertEqual(data['result']['discovery_capabilities'][0]['kind'],'metadata-only; not a measurement adapter')
        self.assertNotIn('uncore-memory',[r['id'] for r in data['result']['tools']])


if __name__=='__main__':unittest.main()
