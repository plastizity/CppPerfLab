// Contract fixture, not production dispatch or a performance workload.
#include <cppperflab/phase.hpp>
#include <stdexcept>

int main(int argc,char** argv) {
    if (argc!=2) return 1;
    perflab::PhaseRecorder recorder(2);
    const auto outer=recorder.define_phase("outer");
    const auto inner=recorder.define_phase("inner\"quoted");
    const auto input=recorder.define_input("text\ninput");
    const auto success=recorder.define_result("uncertified");
    const auto count=recorder.define_counter("modeled_work","count","Modeled");
    std::size_t a,b;
    {
        perflab::PhaseRecorder::Scope parent(recorder,outer,input,true);a=parent.id();
        {
            perflab::PhaseRecorder::Scope child(recorder,inner,input);b=child.id();
            try {recorder.write(argv[1],"unfinished");return 2;}
            catch (const std::logic_error&) {}
        }
    }
    recorder.mark(a,success,true);recorder.mark(b,success,true);
    recorder.set_work(a,count,3);recorder.set_work(b,count,2);
    try {perflab::PhaseRecorder::Scope overflow(recorder,outer,input);return 3;}
    catch (const std::length_error&) {}
    recorder.write(argv[1],"sdk-contract");
}
