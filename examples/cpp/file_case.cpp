// Standalone raw-document byte counter. Model semantics live here, not the runner.
#include <cppperflab/case.hpp>
#include <algorithm>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <string>
#include <vector>

struct Input {std::string bytes;std::optional<std::size_t> expected;};
struct Counter {
    std::size_t accumulated=0;
    std::size_t compute(const std::string& bytes) {
        for (unsigned char c:bytes) accumulated+=c=='x';
        return accumulated;
    }
};
struct Adapter {
    using Output=std::size_t;
    std::vector<perflab::CaseCounter> counter_definitions() const {return {{"input_bytes","bytes","Measured"},{"operations","count","Measured"}};}
    std::unique_ptr<Input> load(const perflab::CaseFiles& files) const {
        if (files.format_version!="raw-bytes-v1") throw std::invalid_argument("Unsupported raw-document schema");
        std::ifstream stream(files.input_path,std::ios::binary);
        if (!stream) throw std::runtime_error("Cannot open captured input");
        auto input=std::make_unique<Input>();
        input->bytes.assign(std::istreambuf_iterator<char>(stream),std::istreambuf_iterator<char>());
        if (files.has_expected()) {
            if (files.expected_format_version!="count-v1" || (files.expected_format!="text" && files.expected_format!="json")) throw std::invalid_argument("Unsupported expected count schema");
            std::ifstream expected(files.expected_path);
            std::size_t value;
            if (!(expected>>value)) throw std::invalid_argument("Invalid expected byte count");
            expected>>std::ws;if (!expected.eof()) throw std::invalid_argument("Trailing expected data");
            input->expected=value;
        }
        return input;
    }
    std::unique_ptr<Counter> prepare(const Input&) const {return std::make_unique<Counter>();}
    void reset(Counter& counter,const Input&) const {counter.accumulated=0;}
    Output invoke(Counter& counter,const Input& input) const {
        // Observable virtual-like dispatch prevents this synthetic repeat loop
        // from silently hoisting its invariant computation out of the timed call.
        std::size_t (Counter::*volatile call)(const std::string&)=&Counter::compute;
        return (counter.*call)(input.bytes);
    }
    perflab::CaseVerdict validate(const Input& input,Output value) const {
        const auto reference=static_cast<std::size_t>(std::count(input.bytes.begin(),input.bytes.end(),'x'));
        const bool valid=value==reference && (!input.expected || value==*input.expected);
        return {valid,valid?"success":"mismatch",{static_cast<double>(input.bytes.size()),1}};
    }
};
int main(int argc,char** argv) {
    if (argc==2 && std::string(argv[1])=="--verify") {
        Adapter a;
        for (const std::string text:{"","x","abc","xxx\n"}) {
            Input in{text,std::nullopt};auto c=a.prepare(in);
            for (unsigned n=0;n<3;++n) {a.reset(*c,in);if (!a.validate(in,a.invoke(*c,in)).passed) return 2;}
        }
        return 0;
    }
    try {
        Adapter adapter;const auto result=perflab::run_case(adapter,perflab::case_options(argc,argv));
        std::cout<<"completed="<<result.completed_calls<<" requested="<<result.requested_calls<<" correctness="<<(result.passed?"passed":"failed")<<'\n';
        return result.passed?0:2;
    } catch (const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
