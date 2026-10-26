// Clock/recording calibration only; excludes allocation, registration and export.
#include <cppperflab/phase.hpp>
#include <chrono>
#include <iostream>
#include <string>
#include <vector>

int main(int argc,char** argv) {
    const auto n=argc==2?std::stoul(argv[1]):10000;
    if (!n || n>1000000) return 1;
    using Clock=std::chrono::steady_clock;
    volatile unsigned long checksum=0;
    std::vector<double> plain(9),instrumented(9);
    for (unsigned repeat=0;repeat<9;++repeat) {
        perflab::PhaseRecorder recorder(n);
        const auto phase=recorder.define_phase("empty");
        const auto input=recorder.define_input("calibration");
        // Alternate order; retain raw observations instead of subtracting from workloads.
        for (unsigned order=0;order<2;++order) {
            const bool record=((repeat+order)%2)==0;
            const auto begin=Clock::now();
            if (record) {
                for (unsigned long i=0;i<n;++i) {
                    perflab::PhaseRecorder::Scope scope(recorder,phase,input);
                    checksum+=i&1;
                }
            } else {
                for (unsigned long i=0;i<n;++i) checksum+=i&1;
            }
            const double seconds=std::chrono::duration<double>(Clock::now()-begin).count();
            (record?instrumented:plain)[repeat]=seconds;
        }
        checksum+=recorder.summed_duration_ns(); // Consume captured durations outside timed loops.
    }
    std::cout.precision(17);
    std::cout<<"{\"schema_version\":1,\"iterations\":"<<n<<",\"checksum\":"<<checksum
             <<",\"evidence_state\":\"Estimated\",\"plain_seconds\":[";
    for (unsigned i=0;i<9;++i) std::cout<<(i?",":"")<<plain[i];
    std::cout<<"],\"instrumented_seconds\":[";
    for (unsigned i=0;i<9;++i) std::cout<<(i?",":"")<<instrumented[i];
    std::cout<<"],\"incremental_ns_per_call\":[";
    for (unsigned i=0;i<9;++i) std::cout<<(i?",":"")<<(instrumented[i]-plain[i])*1e9/n;
    std::cout<<"],\"limitations\":[\"Synthetic empty scopes; not a universal instrumentation cost\","
             <<"\"Includes clock reads and record bookkeeping; excludes construction, registration, marking and export\","
             <<"\"Never subtract these estimates mechanically from workload measurements\"]}\n";
}
