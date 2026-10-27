// Independent synthetic workload for exercising measurement-state contracts.
// Both strategies compute the same result; only the amount of repeated work differs.
#include <cppperflab/phase.hpp>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

struct WorkResult {
    std::uint64_t sum;
    std::uint64_t visits;
};

WorkResult calculate(std::uint64_t size) {
    volatile std::uint64_t sum=0;
    std::uint64_t visits=0;
    for (std::uint64_t i=1;i<=size;++i) {sum+=i;++visits;}
    return {sum,visits};
}

std::uint64_t positive_integer(const char* text) {
    const std::string value(text);
    if (value.empty() || value[0]=='-') throw std::invalid_argument("Expected positive integer");
    std::size_t end=0;
    const auto parsed=std::stoull(value,&end);
    if (end!=value.size() || parsed==0) throw std::invalid_argument("Expected positive integer");
    return parsed;
}

std::uint64_t expected_sum(std::uint64_t size,std::uint64_t rounds) {
    const auto maximum=std::numeric_limits<std::uint64_t>::max();
    if (size==maximum) throw std::invalid_argument("Size exceeds integer result range");
    std::uint64_t a=size,b=size+1;
    if (a%2==0) a/=2;else b/=2;
    if (a>maximum/b || rounds>maximum/(a*b)) throw std::invalid_argument("Result exceeds integer range");
    // Phase work counters are JSON doubles; all declared counts must be exact.
    constexpr std::uint64_t maximum_exact_double=UINT64_C(9007199254740992);
    if (size>maximum_exact_double || rounds>maximum_exact_double/size)
        throw std::invalid_argument("Work count exceeds exact counter range");
    return a*b*rounds;
}

void require_ready(const std::string& path) {
    std::ifstream input(path);
    std::string state;
    if (!(input>>state) || state!="ready") throw std::runtime_error("Consumer state is not ready");
    input>>std::ws;
    if (!input.eof()) throw std::runtime_error("Malformed consumer state");
}

void mark_used(const std::string& path) {
    std::ofstream output(path,std::ios::trunc);
    if (!(output<<"used\n")) throw std::runtime_error("Cannot update consumer state");
    output.close();
    if (!output) throw std::runtime_error("Cannot finish consumer state update");
}

int main(int argc,char** argv) {
    try {
        if (argc!=6) throw std::invalid_argument("Expected STATE_FILE SIZE ROUNDS baseline|candidate REPORT.phases.json");
        const std::string state_path(argv[1]),strategy(argv[4]);
        const auto size=positive_integer(argv[2]),rounds=positive_integer(argv[3]);
        if (strategy!="baseline" && strategy!="candidate") throw std::invalid_argument("Unknown strategy");
        const auto expected=expected_sum(size,rounds);
        require_ready(state_path);

        perflab::PhaseRecorder recorder(1);
        const auto phase=recorder.define_phase("compute");
        const auto input=recorder.define_input("synthetic-input");
        const auto success=recorder.define_result("success");
        const auto mismatch=recorder.define_result("mismatch");
        const auto visits_counter=recorder.define_counter("visits","count");
        WorkResult (*volatile call)(std::uint64_t)=&calculate;
        std::uint64_t total=0,visits=0;
        std::size_t phase_id;
        {
            perflab::PhaseRecorder::Scope scope(recorder,phase,input,true);
            phase_id=scope.id();
            if (strategy=="baseline") {
                for (std::uint64_t i=0;i<rounds;++i) {
                    const auto work=call(size);total+=work.sum;visits+=work.visits;
                }
            } else {
                const auto work=call(size);visits=work.visits;
                for (std::uint64_t i=0;i<rounds;++i) total+=work.sum;
            }
        }
        const bool passed=total==expected;
        recorder.set_work(phase_id,visits_counter,static_cast<double>(visits));
        recorder.mark(phase_id,passed?success:mismatch,passed);
        mark_used(state_path);
        recorder.write(argv[5],"synthetic-validity-workload");
        std::cout<<"{\"schema_version\":1,\"result\":"<<total<<",\"visits\":"<<visits
                 <<",\"strategy\":\""<<strategy<<"\",\"status\":\""<<(passed?"PASS":"FAIL")<<"\"}\n";
        return passed?0:2;
    } catch (const std::exception& error) {
        std::cerr<<error.what()<<'\n';return 1;
    }
}
