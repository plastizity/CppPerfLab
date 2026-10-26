// Standalone integer kernels with independent closed-form oracles and opt-in clock alignment.
#include <cppperflab/phase.hpp>
#include <cstdint>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

using U64=std::uint64_t;
__attribute__((noinline)) U64 kernel_a(U64 n) {U64 sum=0;for (U64 i=0;i<n;++i) sum+=(i&1023)+1;return sum;}
__attribute__((noinline)) U64 kernel_b(U64 n) {U64 sum=0;for (U64 i=0;i<n;++i) sum+=(i&2047)+1;return sum;}
U64 reference(U64 n,U64 period) {const U64 r=n%period;return (n/period)*(period*(period+1)/2)+r*(r+1)/2;}
// Bind this synthetic pure computation to the timed scope: noinline alone does
// not prevent GCC from moving or reusing a pure function result across timers.
U64 opaque(U64 value) {asm volatile("" : "+r"(value) : : "memory");return value;}

int main(int argc,char** argv) {
    try {
        if (argc==2 && std::string(argv[1])=="--verify") {
            for (U64 n:{0,1,17,1023,1024,1025,2048,10001})
                if (kernel_a(n)!=reference(n,1024) || kernel_b(n)!=reference(n,2048)) return 2;
            return 0;
        }
        if (argc!=4) throw std::invalid_argument("Expected N CALLS REPORT");
        const auto n=std::stoull(argv[1]),calls=std::stoull(argv[2]);
        if (!n || !calls || calls>std::numeric_limits<std::size_t>::max()/3) throw std::invalid_argument("Invalid N/call count");
        using Recorder=perflab::PhaseRecorder;
        Recorder recorder(static_cast<std::size_t>(calls)*3,Recorder::TimeSource::LinuxMonotonic);
        const auto model=recorder.define_phase("model"),a=recorder.define_phase("kernel_a"),b=recorder.define_phase("kernel_b");
        const auto input=recorder.define_input("cyclic-input-"+std::to_string(n));
        const auto valid=recorder.define_result("validated"),invalid=recorder.define_result("mismatch");
        const auto operations=recorder.define_counter("elements","count");
        bool passed=true;
        for (U64 call=0;call<calls;++call) {
            std::size_t parent_id,a_id,b_id;U64 x,y;
            {
                Recorder::Scope parent(recorder,model,input,true);parent_id=parent.id();
                {Recorder::Scope scope(recorder,a,input,true);a_id=scope.id();x=opaque(kernel_a(opaque(n)));}
                {Recorder::Scope scope(recorder,b,input,true);b_id=scope.id();y=opaque(kernel_b(opaque(n)));}
            }
            const bool ok=x==reference(n,1024) && y==reference(n,2048);passed=passed && ok;
            for (auto id:{parent_id,a_id,b_id}) recorder.mark(id,ok?valid:invalid,ok);
            recorder.set_work(parent_id,operations,2.0*static_cast<double>(n));
            recorder.set_work(a_id,operations,static_cast<double>(n));recorder.set_work(b_id,operations,static_cast<double>(n));
        }
        recorder.write(argv[3],"synthetic-cpu-phases");
        std::cout<<(passed?"validated":"mismatch")<<'\n';return passed?0:2;
    } catch (const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
