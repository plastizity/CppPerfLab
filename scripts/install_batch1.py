"""Verify and unpack the reviewed batch-1 artifacts locally; no package install hooks."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
downloads = ROOT / "vendor/downloads"
manifest = []


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unpack_tar(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # Published archives are source/binary data only; no links or devices needed.
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if not parts or ".." in parts or Path(member.name).is_absolute():
                raise ValueError("Unsafe archive member: " + member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError("Unsupported archive member: " + member.name)
            relative = Path(*parts[1:])
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source:
                    target.write_bytes(source.read())
                target.chmod(member.mode & 0o777)
    manifest.append({"archive": archive.name, "sha256": fingerprint(archive),
                     "verification": "locally recorded digest, not an upstream signature"})


hyperfine = downloads / "hyperfine-v1.19.0-x86_64-unknown-linux-gnu.tar.gz"
expected = "d5550bbdc240d2aaa8f43ea2dc8b03a306afb30ea8464cd8b2faa9486ed1019e"
if fingerprint(hyperfine) != expected:
    raise ValueError("Hyperfine archive differs from reviewed batch")
unpack_tar(hyperfine, ROOT / "vendor/hyperfine-1.19.0")
unpack_tar(downloads / "batch1/benchmark-v1.9.4.tar.gz", ROOT / "vendor/benchmark-1.9.4")
rpm_root = ROOT / "vendor/rocky-tools"
rpm_root.mkdir(parents=True, exist_ok=True)
for name in ("perf", "libasan", "libubsan", "elfutils-libs", "elfutils-libelf"):
    matches = list((downloads / "batch1/rpms").glob(name + "-[0-9]*.rpm"))
    if len(matches) != 1:
        raise ValueError("Expected exactly one package for " + name)
    package = matches[0]
    verification = subprocess.check_output(["rpm", "-K", str(package)], text=True)
    if "signatures OK" not in verification:
        raise ValueError("RPM signature not verified: " + verification)
    listing = subprocess.check_output(["rpm", "-qpl", str(package)], text=True)
    if any(".." in Path(p).parts for p in listing.splitlines()):
        raise ValueError("Unsafe RPM path")
    metadata = subprocess.check_output(["rpm", "-qp", "--qf", "%{NAME} %{VERSION}-%{RELEASE} %{LICENSE}", str(package)], text=True)
    unpack = subprocess.Popen(["rpm2cpio", str(package)], stdout=subprocess.PIPE)
    extracted = subprocess.run(["cpio", "-idm", "--quiet", "--no-absolute-filenames"],
                               stdin=unpack.stdout, cwd=rpm_root)
    unpack.stdout.close()
    if unpack.wait() or extracted.returncode:
        raise RuntimeError("RPM extraction failed")
    manifest.append({"package": package.name, "metadata": metadata,
                     "sha256": fingerprint(package), "verification": verification})
for name, version in (("asan", "5.0.0"), ("ubsan", "1.0.0")):
    link = rpm_root / "usr/lib64" / ("lib" + name + ".so")
    if link.is_symlink():
        link.unlink()
    link.symlink_to("lib" + name + ".so." + version)
(ROOT / "vendor/batch1-installed.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("Verified data unpacked under vendor; no system packages or scripts installed")
