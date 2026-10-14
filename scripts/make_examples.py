"""Generate host-local experiment configs for the independent C++ fixtures."""
import copy
import json
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
output = root / "workloads"
output.mkdir(exist_ok=True)
binary = root / "build/profile/examples/cpp"
cpu = sorted(os.sched_getaffinity(0))[0]
compiler = subprocess.check_output(["c++", "--version"], text=True).splitlines()[0]
environment = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "BLIS_NUM_THREADS": "1"}
build = {"revision": "workspace artifact fingerprints", "compiler": compiler,
         "flags": ["-O2", "-g", "-DNDEBUG"], "mode": "RelWithDebInfo",
         "artifacts": [str(root / "examples/cpp/workload.cpp"), str(root / "build/profile/compile_commands.json")]}


def save(name, config):
    (output / (name + ".json")).write_text(json.dumps(config, indent=2) + "\n")


base = {"schema_version": 1, "name": "locality-baseline",
        "workload": {"id": "matrix-checksum", "parameters": {"side": 1024, "repeats": 64}, "inputs": []},
        "target": {"argv": [str(binary / "baseline"), "locality", "1024", "64"],
                   "environment": environment, "build": build},
        "measurement": {"tool": "hyperfine", "repetitions": 9, "warmups": 2,
                        "timeout_seconds": 90, "cpu_affinity": [cpu], "options": {}},
        "correctness": {"argv": ["ctest", "--test-dir", str(root / "build/profile"), "--output-on-failure"],
                        "description": "Both variants must pass independent integer checksum/oracle tests"}}
save("locality-baseline", base)
candidate = copy.deepcopy(base)
candidate["name"] = "locality-candidate"
candidate["target"]["argv"][0] = str(binary / "candidate")
save("locality-candidate", candidate)
cpu_profile = copy.deepcopy(base)
cpu_profile["name"] = "cpu-profile"
cpu_profile["measurement"].update(tool="perf-record", repetitions=1, warmups=1)
save("cpu-profile", cpu_profile)
heap = copy.deepcopy(base)
heap["name"] = "heap-profile"
heap["workload"] = {"id": "allocation-fixture", "parameters": {"blocks": 12000, "repeats": 4}, "inputs": []}
heap["target"]["argv"][1:] = ["allocations", "12000", "4"]
heap["measurement"].update(tool="massif", repetitions=1, warmups=0)
save("heap-profile", heap)
compute = copy.deepcopy(base)
compute["name"] = "compute-counters"
compute["workload"] = {"id": "compute-fixture", "parameters": {"steps": 100000, "repeats": 100}, "inputs": []}
compute["target"]["argv"][1:] = ["compute", "100000", "100"]
compute["measurement"].update(tool="perf-stat", repetitions=3, warmups=1,
                              options={"events": ["duration_time", "task-clock", "cycles", "instructions"]})
save("compute-counters", compute)
algorithm = copy.deepcopy(base)
algorithm["name"] = "equal-pairs"
algorithm["workload"] = {"id": "equal-pair-count", "parameters": {"n": 256, "repeats": 64, "distribution": "random"}, "inputs": []}
algorithm["target"]["argv"] = [str(binary / "pairs_baseline"), "256", "64", "random"]
algorithm["target"]["build"]["artifacts"] = [str(root / "examples/cpp/duplicates.cpp"), str(root / "examples/cpp/oracle_pairs.py"), str(root / "build/profile/compile_commands.json")]
algorithm["correctness"] = {"argv": ["python3.11", str(root / "examples/cpp/oracle_pairs.py"), "{candidate}"],
                            "description": "Independent Python Counter oracle: 28 size/distribution cases, three seeds each; exact integer equality"}
save("algorithm-template", algorithm)
save("algorithm-study", {"schema_version": 1, "name": "pair-count-algorithm-growth",
     "template": "algorithm-template.json", "axis": {"parameter": "n", "values": [256, 512, 1024, 2048, 4096], "argument_index": 1},
     "variants": [{"id": label, "executable": str(binary / ("pairs_" + label)), "revision": label + "-workspace",
                   "artifacts": algorithm["target"]["build"]["artifacts"]} for label in ("baseline", "candidate")],
     "budget": 10, "seed": 20261014, "metric": "time.wall",
     "constraints": "Count all equal unordered pairs exactly; no fixture recognition, no approximate equality, same data generator and seeds. Candidate sorting may mutate only its private input."})
print(output)

# Paired native hardware-counter experiments for dimension-wise reporting.
for label in ('baseline', 'candidate'):
    counters = copy.deepcopy(algorithm)
    counters['name'] = 'pair-counters-'+label
    counters['workload']['parameters']['n'] = 1024
    counters['target']['argv'] = [str(binary/('pairs_'+label)), '1024', '64', 'random']
    counters['correctness']['argv'][-1] = counters['target']['argv'][0]
    counters['measurement'].update(tool='perf-stat', repetitions=9, warmups=2,
                                   options={'events':['instructions','cycles','duration_time']})
    save('pair-counters-'+label, counters)

instruction_template = copy.deepcopy(algorithm)
instruction_template['measurement'].update(tool='perf-stat', repetitions=9, warmups=1,
                                           options={'events':['instructions','cycles']})
save('instruction-template', instruction_template)
instruction_study = json.loads((output/'algorithm-study.json').read_text())
instruction_study.update(name='pair-count-instruction-growth',template='instruction-template.json',
                         metric='cpu.instructions')
save('instruction-study', instruction_study)

# Integrated benchmark targets are optional; configurations are useful only after
# building with PERFLAB_GOOGLE_BENCHMARK_ROOT as described in docs/downloads.md.
for label in ("baseline", "candidate"):
    micro = copy.deepcopy(algorithm)
    micro["name"] = "google-pairs-" + label
    micro["workload"] = {"id": "google-equal-pair-count", "parameters": {"n": 1024, "distribution": "random", "seed": 12345}, "inputs": []}
    micro["target"]["argv"] = [str(binary / ("benchmarks_" + label))]
    micro["target"]["build"]["artifacts"] += [str(root / "examples/cpp/benchmark_pairs.cpp")]
    micro["correctness"]["argv"][-1] = str(binary / ("pairs_" + label))
    micro["measurement"].update(tool="google-benchmark", warmups=0, repetitions=9,
                               options={"filter": "^PairCount/1024$", "min_time_seconds": .1, "warmup_seconds": .02})
    save("google-pairs-" + label, micro)
