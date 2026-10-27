"""Readiness fixtures inspect metadata only; no counter or target is executed."""
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab import uncore
from cppperflab.storage import Store


class UncoreReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pmu=self.root/'pmu';self.pmu.mkdir()
        self.cpu=self.root/'cpu';self.cpu.mkdir()
        self.cpuinfo=self.root/'cpuinfo';self.cpuinfo.write_text('vendor_id : Example\nflags : fpu hypervisor\n')
        self.status=self.root/'status';self.status.write_text('Name:\tfixture\nCapEff:\t0000000000000000\n')
        self.paranoid=self.root/'paranoid';self.paranoid.write_text('2\n')
        for name,path in [('PMU_ROOT',self.pmu),('CPU_ROOT',self.cpu),('CPUINFO_PATH',self.cpuinfo),('STATUS_PATH',self.status),('PARANOID_PATH',self.paranoid)]:
            patch=mock.patch.object(uncore,name,path);patch.start();self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def candidate(self,name='uncore_imc_0'):
        base=self.pmu/name
        for sub in ('format','caps','events'): (base/sub).mkdir(parents=True,exist_ok=True)
        (base/'type').write_text('19\n');(base/'cpumask').write_text('0\n')
        (base/'format'/'event').write_text('config:0-7\n');(base/'format'/'umask').write_text('config:8-15\n')
        (base/'events'/'read').write_text('event=0x04,umask=0x03\n')
        (base/'events'/'read.unit').write_text('MiB\n');(base/'events'/'read.scale').write_text('1.5\n')
        return base

    def discover(self):return uncore.discover(self.root/'output')

    def test_no_candidates_is_not_collector_or_permission_success(self):
        (self.pmu/'amd_df_0').mkdir();(self.pmu/'uncore_cha_0').mkdir()
        report=self.discover()
        self.assertEqual(report['inventory']['visible_pmus'],['amd_df_0','uncore_cha_0'])
        self.assertEqual(report['inventory']['candidates'],[])
        self.assertEqual(report['measurement']['reason'],'no_visible_candidate')
        self.assertEqual(report['collector']['evidence_state'],'Unsupported')
        self.assertEqual(report['policy']['permissions']['evidence_state'],'Missing')
        self.assertFalse(report['execution_performed']);self.assertFalse(report['collection_available'])

    def test_unreadable_inventory_is_distinct_from_empty(self):
        actual=Path.iterdir
        def fail(path):
            if path==self.pmu:raise PermissionError(errno.EACCES,'fixture denied',str(path))
            return actual(path)
        with mock.patch.object(Path,'iterdir',fail): report=self.discover()
        self.assertEqual(report['inventory']['evidence_state'],'Missing')
        self.assertEqual(report['inventory']['errno'],errno.EACCES)
        self.assertEqual(report['inventory']['source'],str(self.pmu))
        self.assertEqual(report['measurement']['reason'],'inventory_unavailable')

    def test_candidate_metadata_never_enables_unimplemented_collector(self):
        base=self.candidate();report=self.discover();candidate=report['inventory']['candidates'][0]
        self.assertEqual(candidate['type']['parsed'],{'evidence_state':'Measured','value':19})
        event=candidate['events'][0]
        self.assertEqual(event['encoding_validation']['fields'],{'event':4,'umask':3})
        self.assertEqual(event['unit_validation']['value'],'MiB')
        self.assertEqual(event['scale_validation']['value'],1.5)
        self.assertEqual(report['measurement'],{'evidence_state':'Unsupported','value':None,'reason':'collector_not_implemented'})
        self.assertFalse(report['collection_available'])
        self.assertTrue((self.root/'output'/event['encoding']['evidence']).is_file())
        self.assertEqual((self.root/'output'/event['encoding']['evidence']).read_text(),(base/'events'/'read').read_text())
        self.assertTrue(report['host']['guest_indicator']['value'])

    def test_bad_scale_unit_format_and_missing_metadata_do_not_guess_line_size(self):
        base=self.candidate()
        (base/'events'/'read.unit').write_text('transactions');(base/'events'/'read.scale').write_text('nan')
        (base/'format'/'umask').write_text('config:15-8')
        (base/'type').write_text('not-a-type')
        candidate=self.discover()['inventory']['candidates'][0];event=candidate['events'][0]
        self.assertEqual(candidate['type']['parsed']['evidence_state'],'Invalid')
        self.assertEqual(event['encoding_validation']['evidence_state'],'Invalid')
        self.assertEqual(event['unit_validation']['evidence_state'],'Unsupported')
        self.assertEqual(event['scale_validation']['evidence_state'],'Invalid')
        (base/'events'/'read.scale').unlink();(base/'events'/'read.unit').unlink()
        event=self.discover()['inventory']['candidates'][0]['events'][0]
        self.assertEqual(event['unit_validation']['evidence_state'],'Missing')
        self.assertEqual(event['scale_validation']['evidence_state'],'Missing')
        self.assertNotIn('64',str(event))

    def test_kernel_bare_and_parameterized_event_terms_keep_their_syntax(self):
        base=self.candidate()
        (base/'format'/'edge').write_text('config:18')
        (base/'events'/'read').write_text('event=0x04,edge,umask=?')
        validation=self.discover()['inventory']['candidates'][0]['events'][0]['encoding_validation']
        self.assertEqual(validation['evidence_state'],'Missing')
        self.assertEqual(validation['fields']['edge'],{'syntax':'bare'})
        self.assertEqual(validation['fields']['umask'],{'syntax':'parameter'})
        (base/'events'/'read').write_text('event=0x04,edge')
        validation=self.discover()['inventory']['candidates'][0]['events'][0]['encoding_validation']
        self.assertEqual(validation['evidence_state'],'Measured')
        self.assertEqual(validation['fields']['edge'],{'syntax':'bare'})

    def test_no_topology_does_not_guess_socket_or_node(self):
        self.candidate()
        topology=self.discover()['inventory']['candidates'][0]['representative_topology']
        self.assertEqual(topology['representative_cpu'],0)
        self.assertEqual(topology['package']['parsed']['evidence_state'],'Missing')
        self.assertEqual(topology['die']['parsed']['evidence_state'],'Missing')
        self.assertEqual(topology['nodes']['evidence_state'],'Missing')

    def test_reversed_cpu_ranges_do_not_create_measured_topology(self):
        base=self.candidate()
        for value in ('3-1','0,3-1'):
            (base/'cpumask').write_text(value)
            with self.subTest(value=value):
                topology=self.discover()['inventory']['candidates'][0]['representative_topology']
                self.assertEqual(topology['evidence_state'],'Invalid')
                self.assertNotIn('representative_cpu',topology)

    def test_package_and_die_only_when_cpu_topology_exists(self):
        self.candidate('amd_umc_0')
        topology=self.cpu/'cpu0'/'topology';topology.mkdir(parents=True)
        (topology/'physical_package_id').write_text('2');(topology/'die_id').write_text('1')
        (self.cpu/'cpu0'/'node3').mkdir()
        data=self.discover()['inventory']['candidates'][0]['representative_topology']
        self.assertEqual(data['package']['parsed']['value'],2)
        self.assertEqual(data['die']['parsed']['value'],1)
        self.assertEqual(data['nodes']['values'],[3])

    def test_probe_archives_only_metadata(self):
        store=Store(self.root/'store')
        with mock.patch('subprocess.run',side_effect=AssertionError('unexpected execution')):
            probe=uncore.probe(store)
        folder=Path(probe['evidence_directory'])
        self.assertEqual(json.loads((folder/'probe.json').read_text()),probe)
        self.assertEqual(json.loads((folder/'uncore-readiness.json').read_text()),probe['capabilities']['memory_controller_traffic'])
