"""Small synthetic processes validate wait4 behavior, not performance."""
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


class NativeResourceHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.helper = cls.root / 'native-resource'
        source = Path(__file__).resolve().parents[1] / 'tools/native_resource.c'
        subprocess.run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(cls.helper)],check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def invoke(self, target):
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            path = Path(temp) / 'resource.json'
            result = subprocess.run([str(self.helper),'--output',str(path),'--',*target],capture_output=True,text=True)
            return result, json.loads(path.read_text())

    def test_version_and_explicit_invalid_output(self):
        version = subprocess.run([str(self.helper),'--version'],check=True,capture_output=True,text=True)
        self.assertEqual(version.stdout.strip(),'CppPerfLab native-resource 1')
        bad = subprocess.run([str(self.helper),'--output','relative.json','--','/bin/true'],capture_output=True,text=True)
        self.assertEqual(bad.returncode,2)

    def test_exact_child_wait_status_and_raw_rusage(self):
        path = self.root/'target-pid.txt'
        command = [sys.executable,'-c','import json,os,sys;open(sys.argv[1],"w").write(json.dumps([os.getpid(),os.getpgrp()]))',str(path)]
        result, report = self.invoke(command)
        self.assertEqual(result.returncode,0)
        self.assertEqual(report['collector'],'linux-wait4')
        target_pid, target_group = json.loads(path.read_text())
        self.assertEqual(report['child_pid'],target_pid)
        self.assertEqual(target_group,os.getpgrp())
        self.assertEqual(report['wait_status'],0)
        self.assertTrue(report['exited']);self.assertEqual(report['exit_code'],0)
        self.assertIsNone(report['signal']);self.assertIsNone(report['exec_errno'])
        usage=report['rusage']
        for name in ('max_rss_kib','minor_faults','major_faults','voluntary_context_switches','involuntary_context_switches','filesystem_inputs','filesystem_outputs'):
            self.assertIsInstance(usage[name],int);self.assertGreaterEqual(usage[name],0)
        for name in ('user_time','system_time'):
            self.assertIsInstance(usage[name]['seconds'],int)
            self.assertGreaterEqual(usage[name]['seconds'],0)
            self.assertGreaterEqual(usage[name]['microseconds'],0)
            self.assertLess(usage[name]['microseconds'],1000000)

    def test_failed_target_and_failed_exec_preserve_distinct_evidence(self):
        result, report = self.invoke([sys.executable,'-c','import sys;sys.exit(7)'])
        self.assertEqual((result.returncode,report['exit_code'],report['exec_errno']),(7,7,None))
        result, report = self.invoke([str(self.root/'no-such-executable')])
        self.assertEqual((result.returncode,report['exit_code'],report['exec_errno']),(127,127,errno.ENOENT))
        self.assertIn('exec ',result.stderr)

    def test_signal_preserves_wait_status_and_propagates_exit(self):
        result, report = self.invoke([sys.executable,'-c','import os,signal;os.kill(os.getpid(),signal.SIGTERM)'])
        self.assertEqual(result.returncode,128+signal.SIGTERM)
        self.assertFalse(report['exited']);self.assertIsNone(report['exit_code'])
        self.assertEqual(report['signal'],signal.SIGTERM)
        self.assertIsNone(report['exec_errno'])
