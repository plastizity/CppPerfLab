"""Configuration boundaries, path precedence and actual availability semantics."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cppperflab.adapters import REGISTRY
from cppperflab.errors import LabError
from cppperflab.tool_config import load_tool_config, tool_configuration, configuration_status


class ToolConfigurationTests(unittest.TestCase):
    def write(self, root, tools, version=1):
        path = Path(root)/'tools.json'
        path.write_text(json.dumps({'schema_version':version, 'tools':tools}))
        return path

    def test_relative_paths_resolve_against_configuration_not_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'hyperfine':{'executable':'bin/hyperfine'}})
            loaded = load_tool_config(path, REGISTRY)
            self.assertEqual(loaded['tools']['hyperfine']['executable'], str(Path(directory)/'bin/hyperfine'))

    def test_missing_explicit_tool_does_not_fall_back_to_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'hyperfine':{'executable':'missing'}})
            with mock.patch.dict('os.environ', {'CPPPERFLAB_HYPERFINE_PATH':'/bin/true'}):
                with tool_configuration(path, REGISTRY):
                    self.assertIsNone(REGISTRY['hyperfine'].tool_path())
                    probe = REGISTRY['hyperfine'].probe(Path(directory)/'probe')
                    self.assertFalse(probe['available'])
                    self.assertEqual(probe['reason'], 'configured_path_missing_or_not_usable')
                self.assertEqual(REGISTRY['hyperfine'].tool_path(), '/bin/true')

    def test_valid_path_is_not_a_successful_collection_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'hyperfine':{'executable':'/bin/true'}})
            with tool_configuration(path, REGISTRY):
                self.assertTrue(configuration_status()['tools']['hyperfine']['path_usable'])
                probe = REGISTRY['hyperfine'].probe(Path(directory)/'probe')
                self.assertFalse(probe['available'])

    def test_unknown_fields_tool_ids_and_versions_are_rejected(self):
        cases = [({'future':{'executable':'/bin/true'}},1),
                 ({'hyperfine':{'executable':'/bin/true','install':True}},1),
                 ({'hyperfine':{'executable':'/bin/true'}},2),
                 ({'google-benchmark':{'executable':'/bin/true'}},1)]
        with tempfile.TemporaryDirectory() as directory:
            for tools, version in cases:
                with self.subTest(tools=tools), self.assertRaises(LabError):
                    load_tool_config(self.write(directory,tools,version),REGISTRY)

    def test_google_benchmark_prefix_is_only_a_build_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {'google-benchmark':{'build_prefix':'.'}})
            with tool_configuration(path, REGISTRY):
                status = configuration_status()['tools']['google-benchmark']
                self.assertTrue(status['path_usable'])
                self.assertIn('Build hint only', status['meaning'])
                probe = REGISTRY['google-benchmark'].probe(Path(directory)/'probe')
                self.assertFalse(probe['available'])
                self.assertEqual(probe['reason'], 'integrated_target_required')
