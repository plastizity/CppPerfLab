"""Offline project-local zipapp installation, using Python's supported zipapp API."""
from pathlib import Path
import shutil
import tempfile
import zipapp

root = Path(__file__).resolve().parents[1]
destination = root / ".local/bin/perflab"
destination.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="cppperflab-install-") as staging:
    shutil.copytree(root / "src/cppperflab", Path(staging) / "cppperflab",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (Path(staging) / "__main__.py").write_text(
        "from cppperflab.cli import main\nraise SystemExit(main())\n")
    zipapp.create_archive(staging, target=destination, interpreter="/usr/bin/env python3.11",
                          compressed=True)
destination.chmod(0o755)
print(destination)
