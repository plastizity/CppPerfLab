# Project-local dependencies, in batches

The execution environment could not resolve GitHub, Rocky, Aliyun or Tsinghua
mirror hostnames. No network script was executed. The user downloaded Hyperfine
and batch 1. Archives/RPMs stay under `vendor/downloads`; only selected packages
are unpacked, never installed into the system. The large DNF dependency closure
is not all activated. Do not add its glibc, systemd or other system libraries to
the runtime path.

## Reviewed current batch

- Hyperfine 1.19.0 official Linux x86_64 archive; MIT OR Apache-2.0. Reviewed SHA-256
  `d5550bbdc240d2aaa8f43ea2dc8b03a306afb30ea8464cd8b2faa9486ed1019e` is an integrity
  fingerprint, not a publisher signature. Licenses are kept in its local directory.
- Google Benchmark 1.9.4 official source archive; Apache-2.0. Project/CMake/LICENSE
  inspected locally, dependency downloading disabled. GCC 8 emits a known-style
  `suggest-override` warning on upstream `final`; `BENCHMARK_ENABLE_WERROR=OFF` is
  used for this third-party build only. No upstream source was patched.
- Rocky perf 4.18.0-553.172.1.el8_10; RPM signature/digests verified. It has libelf
  and libdw/DWARF. Used explicitly for perf-record through
  `CPPPERFLAB_PERF_RECORD_PATH`; system perf 6.18 remains the stat implementation.
- Rocky libasan/libubsan 8.5.0-29 and elfutils runtime 0.190-2; RPMs verified and
  unpacked under `vendor/rocky-tools`. GCC is 8.5.0-28; local runtime compatibility
  is checked through the separate sanitizer build. Package licenses are recorded
  in `vendor/batch1-installed.json`.

```bash
python3.11 scripts/install_batch1.py
cmake -S vendor/benchmark-1.9.4 -B build/google-benchmark -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DBENCHMARK_ENABLE_TESTING=OFF \
  -DBENCHMARK_ENABLE_GTEST_TESTS=OFF -DBENCHMARK_DOWNLOAD_DEPENDENCIES=OFF \
  -DBENCHMARK_ENABLE_WERROR=OFF \
  -DCMAKE_INSTALL_PREFIX="$PWD/vendor/google-benchmark-install"
cmake --build build/google-benchmark -j 2
cmake --install build/google-benchmark
cmake --preset profile \
  -DPERFLAB_GOOGLE_BENCHMARK_ROOT="$PWD/vendor/google-benchmark-install"
cmake --build --preset profile -j 2
python3.11 scripts/make_examples.py
bash scripts/install.sh
source scripts/env.sh
perflab run --config workloads/google-pairs-baseline.json --json
perflab run --config workloads/google-pairs-candidate.json --json
```

Google Benchmark requires an integrated executable and an explicit single-case
filter; it is not a general external-command profiler. `tools probe` without a
target reports `integrated_target_required`. Planning lists registered target
cases; actual collection is validated after the oracle in `run`.

## Correctness runtime limitation

The missing GCC libasan caused the original check build to fail. Local extraction
fixed linking. LeakSanitizer cannot operate under the execution sandbox's ptrace
restrictions. The explicit separate command below checks ASan and UBSan **without
leak detection**; it is not a passing leak check:

```bash
cmake --preset check
cmake --build --preset check
ASAN_OPTIONS=detect_leaks=0 ctest --preset check
```

## Later batches

2. Heaptrack: allocation counts, temporary allocations and stacks. Select a fixed
   source/release after checking Qt-free CLI build dependencies and output parser.
3. pyATF: parameter search after the evaluator/correctness contracts and finite
   budget are validated; do not replace run storage with another benchmark system.
4. Perfetto: thread/task timeline. Evolution frameworks wait for verified
   filesystem/network isolation and candidate/API budgets.

These later batches are not downloaded, installed or represented as available
adapters. Do not request more downloads until the current batch's evidence is read.
