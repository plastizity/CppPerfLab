// Standalone dyadic dot-product benchmark with an independent integer oracle.
#include <benchmark/benchmark.h>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

__attribute__((noinline)) static double dot_product(const double* a, const double* b, std::size_t n) {
#if DOT_UNROLLED
    double s0=0, s1=0, s2=0, s3=0;
    std::size_t i=0;
    for (; i+3<n; i+=4) {
        s0+=a[i]*b[i]; s1+=a[i+1]*b[i+1];
        s2+=a[i+2]*b[i+2]; s3+=a[i+3]*b[i+3];
    }
    double sum=(s0+s1)+(s2+s3);
    for (; i<n; ++i) sum+=a[i]*b[i];
    return sum;
#else
    double sum=0;
    for (std::size_t i=0; i<n; ++i) sum+=a[i]*b[i];
    return sum;
#endif
}

static double prepare(std::vector<double>& a, std::vector<double>& b) {
    std::int64_t exact=0;
    for (std::size_t i=0; i<a.size(); ++i) {
        const auto x=static_cast<std::int64_t>(i%17)-8;
        const auto y=static_cast<std::int64_t>(i%13)-6;
        a[i]=static_cast<double>(x)/16; b[i]=static_cast<double>(y)/16;
        exact+=x*y;
    }
    return static_cast<double>(exact)/256;
}

static bool verify() {
    for (auto n:{1u,2u,3u,4u,7u,17u,127u,1024u,4096u,65536u}) {
        std::vector<double> a(n),b(n);
        const double expected=prepare(a,b);
        if (dot_product(a.data(),b.data(),n)!=expected) return false;
    }
    return true;
}

static void Dot(benchmark::State& state) {
    const auto n=static_cast<std::size_t>(state.range(0));
    std::vector<double> a(n),b(n);
    const double expected=prepare(a,b);
    double result=dot_product(a.data(),b.data(),n);
    if (result!=expected) {state.SkipWithError("integer oracle mismatch");return;}
    for (auto iteration:state) {
        (void)iteration;
        benchmark::DoNotOptimize(a.data()); benchmark::DoNotOptimize(b.data());
        result=dot_product(a.data(),b.data(),n);
        benchmark::DoNotOptimize(result); benchmark::ClobberMemory();
    }
    if (result!=expected) state.SkipWithError("timed output mismatch");
    // Plain per-iteration algorithm counts, never rates or total-iteration counts.
    state.counters["useful_flops_per_iteration"]=static_cast<double>(2*n);
    state.counters["logical_bytes_per_iteration"]=static_cast<double>(2*n*sizeof(double));
    state.counters["operations_per_iteration"]=1;
}
BENCHMARK(Dot)->Arg(1024)->Arg(4096)->Arg(65536);

int main(int argc,char** argv) {
    if (argc==2 && std::string(argv[1])=="--verify") {
        if (!verify()) return 2;
        std::cout<<"exact dyadic oracle passed: ten sizes\n"; return 0;
    }
    benchmark::Initialize(&argc,argv);
    if (benchmark::ReportUnrecognizedArguments(argc,argv)) return 1;
    benchmark::RunSpecifiedBenchmarks(); benchmark::Shutdown();
    return 0;
}
