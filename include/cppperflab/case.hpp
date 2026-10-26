#pragma once
// File-driven C++17 adapter runner. No compiler, tool scheduler or business decoder.
#include <cppperflab/phase.hpp>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>
#include <limits>
#include <stdexcept>

namespace perflab {
struct CaseFiles {
    std::string input_path,expected_path,id,format,format_version,expected_format,expected_format_version;
    bool has_expected() const {return !expected_path.empty();}
};
struct CaseOptions {
    CaseFiles files;
    std::string lifecycle,report_path;
    std::size_t repeats=0;
};
struct CaseCounter {std::string name,unit,evidence_state="Measured";};
struct CaseVerdict {bool passed;std::string classification;std::vector<double> work;};
struct CaseSummary {bool passed;std::size_t requested_calls,completed_calls;};

inline CaseOptions case_options(int argc,char** argv) {
    if (argc!=12) throw std::invalid_argument("Expected --case-v1 input expected-or-dash id input-format input-version expected-format expected-version lifecycle repeats report");
    // argc includes a reserved explicit ABI marker so accidental argument shifts fail.
    if (std::string(argv[1])!="--case-v1") throw std::invalid_argument("Expected --case-v1");
    CaseOptions o;
    o.files={argv[2],std::string(argv[3])=="-"?"":argv[3],argv[4],argv[5],argv[6],
             std::string(argv[7])=="-"?"":argv[7],std::string(argv[8])=="-"?"":argv[8]};
    o.lifecycle=argv[9];o.report_path=argv[11];
    const std::string count(argv[10]);
    if (count.empty() || count.find_first_not_of("0123456789")!=std::string::npos) throw std::invalid_argument("Invalid case repeats");
    const auto parsed=std::stoull(count);
    if (!parsed || parsed>std::numeric_limits<std::size_t>::max()) throw std::invalid_argument("Case repeats exceed record capacity range");
    o.repeats=static_cast<std::size_t>(parsed);
    return o;
}

template<class Adapter> CaseSummary run_case(Adapter& adapter,const CaseOptions& options) {
    using Scope=PhaseRecorder::Scope;
    using Output=typename Adapter::Output;
    if (!options.repeats || options.repeats>(std::numeric_limits<std::size_t>::max()-4)/5) throw std::invalid_argument("Invalid repeat count");
    if (options.lifecycle!="per_call" && options.lifecycle!="reuse_reset") throw std::invalid_argument("Invalid lifecycle");
    if (options.files.id.empty() || options.files.input_path.empty() || options.files.format_version.empty() || options.report_path.empty()) throw std::invalid_argument("Missing case identity/path/version");
    if (options.files.format!="text" && options.files.format!="json" && options.files.format!="binary") throw std::invalid_argument("Unknown input format");
    if (options.files.has_expected()) {
        if (options.files.expected_format_version.empty() || (options.files.expected_format!="text" && options.files.expected_format!="json" && options.files.expected_format!="binary")) throw std::invalid_argument("Expected payload format/version required");
    } else if (!options.files.expected_format.empty() || !options.files.expected_format_version.empty()) throw std::invalid_argument("Unexpected reference metadata without an expected payload");
    PhaseRecorder recorder(options.repeats*5+4);
    const auto unload=recorder.define_phase("unload");
    const auto load=recorder.define_phase("load"),prepare=recorder.define_phase("prepare"),reset=recorder.define_phase("reset");
    const auto operation=recorder.define_phase("operation"),invoke=recorder.define_phase("invoke"),validate=recorder.define_phase("validate"),teardown=recorder.define_phase("teardown");
    const auto input_id=recorder.define_input(options.files.id);
    const auto normal=recorder.define_result("completed");
    const auto counters=adapter.counter_definitions();
    std::vector<std::size_t> work_ids;
    for (const auto& c:counters) work_ids.push_back(recorder.define_counter(c.name,c.unit,c.evidence_state));
    std::map<std::string,std::size_t> classifications;
    decltype(adapter.load(options.files)) input;
    std::size_t load_id;
    {Scope scope(recorder,load,input_id,true);load_id=scope.id();input=adapter.load(options.files);}
    if (!input) throw std::logic_error("Adapter::load must return an owned nonnull input handle");
    recorder.mark(load_id,normal,true);
    decltype(adapter.prepare(*input)) context;
    const bool reuse=options.lifecycle=="reuse_reset";
    if (reuse) {
        std::size_t id;
        {Scope scope(recorder,prepare,input_id,true);id=scope.id();context=adapter.prepare(*input);}
        if (!context) throw std::logic_error("Adapter::prepare must return a nonnull context handle");
        recorder.mark(id,normal,true);
    }
    bool passed=true;std::size_t completed=0;
    for (std::size_t i=0;i<options.repeats;++i) {
        std::size_t parent_id,preparation_id,invoke_id,validation_id,teardown_id;
        CaseVerdict verdict;
        {
            Scope parent(recorder,operation,input_id,true);parent_id=parent.id();
            {
                Scope scope(recorder,reuse?reset:prepare,input_id,true);preparation_id=scope.id();
                if (reuse) adapter.reset(*context,*input);else context=adapter.prepare(*input);
            }
            if (!reuse && !context) throw std::logic_error("Adapter::prepare returned null context");
            std::optional<Output> output;
            {Scope scope(recorder,invoke,input_id,true);invoke_id=scope.id();output.emplace(adapter.invoke(*context,*input));}
            {Scope scope(recorder,validate,input_id,true);validation_id=scope.id();verdict=adapter.validate(*input,*output);}
            {Scope scope(recorder,teardown,input_id,true);teardown_id=scope.id();output.reset();if (!reuse) context.reset();}
        }
        if (verdict.classification.empty() || verdict.work.size()!=work_ids.size()) throw std::logic_error("Invalid adapter verdict/classification/work contract");
        auto found=classifications.find(verdict.classification);
        if (found==classifications.end()) found=classifications.emplace(verdict.classification,recorder.define_result(verdict.classification)).first;
        for (auto id:{parent_id,preparation_id,invoke_id,validation_id,teardown_id}) recorder.mark(id,found->second,verdict.passed);
        for (std::size_t c=0;c<work_ids.size();++c) {
            recorder.set_work(parent_id,work_ids[c],verdict.work[c]);recorder.set_work(invoke_id,work_ids[c],verdict.work[c]);
        }
        ++completed;passed=passed && verdict.passed;
        if (!passed) break; // Preserve first failed input and stop, no retry.
    }
    if (reuse) {
        std::size_t id;{Scope scope(recorder,teardown,input_id,true);id=scope.id();context.reset();}
        recorder.mark(id,normal,passed);
    }
    std::size_t unload_id;{Scope scope(recorder,unload,input_id,true);unload_id=scope.id();input.reset();}
    recorder.mark(unload_id,normal,passed);
    recorder.write(options.report_path,"case-lifecycle");
    return {passed,options.repeats,completed};
}
} // namespace perflab
