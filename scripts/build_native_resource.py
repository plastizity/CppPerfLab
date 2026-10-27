#!/usr/bin/env python3
"""Explicitly build the repository-owned wait4 helper; never build a consumer."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parent.parent
    source = root / 'tools' / 'native_resource.c'
    binary = root / 'build' / 'native-resource'
    installed = root / '.local' / 'bin' / 'perflab-resource'
    binary.parent.mkdir(parents=True, exist_ok=True)
    installed.parent.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('gcc')
    if compiler is None:
        raise SystemExit('gcc is required to build native-resource')
    flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror']
    compiler_version = subprocess.run([compiler, '--version'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    subprocess.run([compiler, *flags, str(source), '-o', str(binary)], check=True)
    shutil.copy2(binary, installed)
    manifest = {'schema_version': 1, 'version': 'CppPerfLab native-resource 1',
                'source': str(source), 'source_sha256': digest(source),
                'compiler': compiler, 'compiler_version': compiler_version, 'flags': flags,
                'binary': str(binary), 'binary_sha256': digest(binary), 'installed_binary': str(installed),
                'installed_sha256': digest(installed)}
    (binary.parent / 'native-resource.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(str(installed))


if __name__ == '__main__':
    main()
