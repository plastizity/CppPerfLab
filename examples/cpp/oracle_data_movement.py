#!/usr/bin/env python3.11
"""Independent exact reference and target work-protocol check for the synthetic example."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

binary = sys.argv[1]
subprocess.run([binary, '--verify'], check=True, capture_output=True, text=True)
with tempfile.TemporaryDirectory(prefix='cppperflab-data-movement-') as folder:
    report = Path(folder) / 'sum.work.json'
    for mode in ('seq', 'random', 'aos', 'soa'):
        for size in (1, 7, 257):
            for rounds in (1, 3):
                for seed in (0, 42):
                    result = subprocess.run([binary, mode, str(size), str(rounds), str(seed), str(report)],
                                            check=True, capture_output=True, text=True)
                    output = json.loads(result.stdout)
                    if output != {'sum': size * (size + 1) // 2 * rounds, 'count': size * rounds}:
                        raise RuntimeError(f'{mode}: exact result mismatch')
                    expected = {'schema_version': 1, 'definition_id': 'sum-items-v1', 'unit': 'item',
                                'count': size * rounds, 'scope': 'target-invocation',
                                'count_kind': 'observed', 'completed': True}
                    if json.loads(report.read_text(encoding='utf-8')) != expected:
                        raise RuntimeError(f'{mode}: invalid work report')
print('Synthetic data movement and independent integer oracle passed')
