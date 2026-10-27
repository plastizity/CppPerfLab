# CppPerfLab

CppPerfLab is C++ performance analysis infrastructure for Linux/WSL2. It runs
reviewed, prebuilt workloads with available measurement tools, preserves raw
evidence, and compares results under explicit correctness and compatibility
conditions. It supports both existing programs and file-driven function/class
experiments.

## Get started

Python 3.11+ and at least one compatible measurement tool are required. The
project-local installer is offline and does not modify global configuration.

**Start with the [usage guide](USAGE.md)** for installation, workload setup,
execution budgets, experiments, human and Agent reports, and the topic guide index.
It also explains how to integrate a consuming project without an implicit rebuild.

- [Agent entry and project rules](AGENTS.md)
- [Performance domains and metrics specification](docs/performance_domain_metrics_spec.md)
- [Architecture](docs/architecture.md), [protocol](docs/protocol.md), and [core metrics](docs/core-metrics.md)
- [Historical validation](docs/history/acceptance.md) and [feedback implementation](docs/history/feedback-implementation.md)

## Development

Select tests relevant to the change, for example:

```bash
PYTHONPATH=src:tests python3.11 -m unittest test_views -q
```

Native examples use CMake; building them is an explicit operation. Measurement
availability depends on the installed tool, platform, hardware, and permissions.
See [local dependencies](guides/downloads.md) and the [usage guide](USAGE.md).

## License and scope

Platform source is MIT licensed; external tools retain their own licenses. The
platform does not automatically modify or rebuild consuming projects. Process
controls are not a security sandbox for untrusted code. A passing correctness or
regression gate does not by itself establish a performance improvement.
