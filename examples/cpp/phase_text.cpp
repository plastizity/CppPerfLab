// Synthetic function-level input replay; file parsing and oracle stay outside invoke.
#include <cppperflab/phase.hpp>
#include <algorithm>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

class TextCounter {
public:
    std::size_t invoke(const std::string& input) const {
        std::size_t count=0;
        for (unsigned char c:input) count+=c=='x';
        return count;
    }
};
int main(int argc,char** argv) {
    TextCounter counter;
    if (argc==2 && std::string(argv[1])=="--verify") {
        for (auto input:{"","x","abc","x x\\nx"}) {
            const std::string text(input);
            if (counter.invoke(text)!=static_cast<std::size_t>(std::count(text.begin(),text.end(),'x'))) return 2;
        }
        return 0;
    }
    if (argc!=4) return 1;
    std::ifstream file(argv[1],std::ios::binary);
    if (!file) return 1;
    const std::string text((std::istreambuf_iterator<char>(file)),std::istreambuf_iterator<char>());
    const auto repeats=std::stoul(argv[2]);
    if (!repeats || repeats>10000) return 1;
    perflab::PhaseRecorder recorder(repeats*2);
    const auto parent=recorder.define_phase("call");
    const auto invoke=recorder.define_phase("invoke");
    const auto input=recorder.define_input("text-input");
    const auto success=recorder.define_result("success");
    const auto mismatch=recorder.define_result("mismatch");
    const auto characters=recorder.define_counter("input_bytes","bytes");
    std::size_t (TextCounter::*volatile call)(const std::string&) const=&TextCounter::invoke;
    bool valid=true;
    for (unsigned long i=0;i<repeats;++i) {
        std::size_t outer_id,inner_id,result;
        {
            perflab::PhaseRecorder::Scope outer(recorder,parent,input,true);outer_id=outer.id();
            {
                perflab::PhaseRecorder::Scope inner(recorder,invoke,input,true);inner_id=inner.id();
                result=(counter.*call)(text);
            }
        }
        const bool passed=result==static_cast<std::size_t>(std::count(text.begin(),text.end(),'x'));
        recorder.set_work(outer_id,characters,static_cast<double>(text.size()));
        recorder.set_work(inner_id,characters,static_cast<double>(text.size()));
        recorder.mark(outer_id,passed?success:mismatch,passed);
        recorder.mark(inner_id,passed?success:mismatch,passed);
        valid=valid && passed;
    }
    recorder.write(argv[3],"text-function");
    std::cout<<(valid?"validated":"mismatch")<<'\n';return valid?0:2;
}
