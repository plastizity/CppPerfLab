// Synthetic copy-and-reduce pipeline with an explicitly instrumented stage.
#include <benchmark/benchmark.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

static std::uint64_t sum(const std::vector<std::uint64_t>& input) {
    std::uint64_t result=0;
    for (auto x:input) result+=x;
    return result;
}
static void Pipeline(benchmark::State& state) {
    const std::size_t n=static_cast<std::size_t>(state.range(0));
    std::vector<std::uint64_t> input(n,7),temporary(n);
    double copy_seconds=0;
    for (auto iteration:state) {
        (void)iteration;
        auto begin=std::chrono::steady_clock::now();
        std::copy(input.begin(),input.end(),temporary.begin());
        auto end=std::chrono::steady_clock::now();
        copy_seconds+=std::chrono::duration<double>(end-begin).count();
        benchmark::ClobberMemory();
        auto result=sum(temporary);
        benchmark::DoNotOptimize(result);
        if (result!=7*n) {state.SkipWithError("exact reduction mismatch");break;}
    }
    state.counters["copy_seconds"]=copy_seconds/static_cast<double>(state.iterations());
    state.counters["copied_bytes"]=static_cast<double>(n*sizeof(std::uint64_t));
    state.counters["traversals"]=2;
    state.counters["operations"]=1;
}
BENCHMARK(Pipeline)->Arg(1048576)->UseRealTime();
int main(int argc,char** argv) {
    if (argc==2 && std::string(argv[1])=="--verify") {
        for (auto n:{1u,17u,1024u}) {
            std::vector<std::uint64_t> input(n,7),output(n);
            std::copy(input.begin(),input.end(),output.begin());
            if (sum(output)!=7*n) return 2;
        }
        std::cout<<"exact copy/reduction oracle passed\n";return 0;
    }
    benchmark::Initialize(&argc,argv);
    if (benchmark::ReportUnrecognizedArguments(argc,argv)) return 1;
    benchmark::RunSpecifiedBenchmarks();benchmark::Shutdown();
}
