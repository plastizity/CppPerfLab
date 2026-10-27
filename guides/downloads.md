# Optional project-local dependencies

CppPerfLab can use existing tool installations through `tools.local.json`.
The optional offline recipe below unpacks a fixed set of reviewed archives/RPMs
under `vendor`; it does not install system packages or execute download scripts.
Only the selected packages are activated. Do not add unrelated glibc/systemd
libraries from a dependency bundle to the runtime path.

## Supported offline recipe

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

Sanitizer builds require matching compiler runtimes. Some restricted execution
environments prevent LeakSanitizer from working. If leak detection is unavailable,
the separate command below checks ASan and UBSan **without leak detection**;
it does not constitute a passing leak check:

```bash
cmake --preset check
cmake --build --preset check
ASAN_OPTIONS=detect_leaks=0 ctest --preset check
```

Future tool choices and their boundaries are in [tool selection](../docs/tool-selection.md).

## Native resource helper

The project-owned, MIT-licensed helper can be built explicitly with
`python3.11 scripts/build_native_resource.py`. This compiles only the small local
C helper with GCC, installs it under `.local/bin` and records a build manifest;
it downloads no package and does not build a consuming project. See
[native resources](native-resources.md) for the adapter and scope.

Attempts to obtain GNU time from the official Rocky and GNU FTP sources timed out;
no package was obtained, signature verified or installed. Existing Google Benchmark
1.9.4 and Valgrind 3.22 can be reused where available: Cachegrind remains Modeled,
and Massif/DHAT are instrumented observations. Any later external package still
requires source, version, license and security review.
