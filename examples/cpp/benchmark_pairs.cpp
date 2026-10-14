// Google Benchmark is explicitly integrated only in this standalone test target.
#define PAIRS_NO_MAIN
#include "duplicates.cpp"
#include <benchmark/benchmark.h>

static void PairCount(benchmark::State& state) {
    const auto original = input(static_cast<std::size_t>(state.range(0)), 12345, "random");
    for (auto iteration : state) {
        (void)iteration;
        auto values = original;  // Same copy cost and private input for both algorithms.
        benchmark::DoNotOptimize(values.data());
        auto result = pairs(values);
        benchmark::DoNotOptimize(result);
        benchmark::ClobberMemory();
    }
    state.SetComplexityN(state.range(0));
}
BENCHMARK(PairCount)->Arg(256)->Arg(512)->Arg(1024)->Arg(2048)->Arg(4096)->Complexity();
BENCHMARK_MAIN();
