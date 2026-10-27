"""POSIX execution boundary: argv only, bounded lifetime, logs survive failure."""
import os
from pathlib import Path
import resource
import signal
import subprocess
import time
from contextvars import ContextVar

from .errors import LabError


_execution_events = ContextVar('cppperflab_execution_events', default=None)

def kill_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass  # The permitted race is that the owned process group already exited.


def execute(argv, directory, *, cwd=None, env=None, timeout=60, affinity=None,
            limits=None, runtime_identity=None, identity_target=None, identity_direct=False,on_start=None,on_exit=None):
    """Return execution facts; nonzero child status is interpreted by the adapter.

    Each child gets its own session. Detached/daemonizing workloads are unsupported.
    Elapsed launcher time is diagnostic only, never an adapter's timing metric.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "posix" or not hasattr(os, "sched_setaffinity"):
        raise LabError("unavailable", "Execution requires Linux/WSL; Windows native is not supported")
    child_env = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
    child_env.update(env or {})
    limits = limits or {}

    def child_setup():
        if affinity is not None:
            os.sched_setaffinity(0, affinity)
        if "address_space_bytes" in limits:
            n = limits["address_space_bytes"]
            resource.setrlimit(resource.RLIMIT_AS, (n, n))
        if "cpu_seconds" in limits:
            n = limits["cpu_seconds"]
            resource.setrlimit(resource.RLIMIT_CPU, (n, n))

    started = time.monotonic()
    with (directory / "stdout.txt").open("wb") as stdout, (directory / "stderr.txt").open("wb") as stderr:
        try:
            process = subprocess.Popen(argv, cwd=cwd or directory, env=child_env,
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, preexec_fn=child_setup)
        except PermissionError as error:
            raise LabError("permission_denied", str(error)) from error
        except (OSError, subprocess.SubprocessError) as error:
            raise LabError("tool_failed", "Could not launch process", {"reason": str(error), "argv": argv}) from error
        status = "completed"
        observer = None
        started_ns=time.monotonic_ns()
        execution_events=_execution_events.get()
        deadline=time.monotonic()+timeout
        try:
            if execution_events is not None:
                execution_events('start',dict(pid=process.pid,argv=argv,directory=str(directory),started_monotonic_ns=started_ns))
            if on_start is not None:on_start(process.pid)
            if runtime_identity is None:
                process.wait(timeout=max(0,deadline-time.monotonic()))
            else:
                from .runtime_identity import RuntimeObserver
                observer=RuntimeObserver(directory,runtime_identity,identity_target,identity_direct)
                observer.root_pid=process.pid
                while process.poll() is None:
                    if observer.active:observer.sample(process.pid)
                    if not observer.active and process.poll() is None:observer.truncations.add('max_snapshots')
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        status='timeout';break
                    interval=runtime_identity['interval_ms']/1000 if observer.active else remaining
                    try:process.wait(timeout=min(remaining,interval))
                    except subprocess.TimeoutExpired:
                        if time.monotonic()>=deadline:status='timeout';break
        except subprocess.TimeoutExpired:
            status = "timeout"
        except KeyboardInterrupt:
            status = "cancelled"
        finally:
            # Also clean descendants after an early parent exit. No process may outlive a run.
            kill_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                kill_group(process.pid, signal.SIGKILL)
                process.wait()
            kill_group(process.pid, signal.SIGKILL)
            ended_ns=time.monotonic_ns()
            record={"argv":argv,"cwd":str(cwd or directory),"environment":child_env,"pid":process.pid,
                "returncode":process.returncode,"status":status,"started_monotonic_ns":started_ns,"ended_monotonic_ns":ended_ns,
                "monotonic_scope":"observed launcher-command lifetime after Popen through cleanup; excludes postprocessing"}
            try:
                if execution_events is not None:execution_events('end',record)
                if on_exit is not None:on_exit(record)
            finally:
                if observer is not None:observer.finish()
    return {**record,
            "orchestrator_elapsed_seconds": time.monotonic() - started,
            "timing_scope": "diagnostic launcher lifetime; not a target performance metric"}


def check_execution(record):
    if record["status"] != "completed":
        raise LabError(record["status"], "Execution " + record["status"], record)
