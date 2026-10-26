#pragma once
// Optional C++11 instrumentation. Register names/inputs/results before hot calls.
// One recorder per thread; scopes must be properly nested and finish before write().
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cmath>
#include <limits>
#include <utility>
#include <functional>
#include <algorithm>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef __linux__
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
#endif

namespace perflab {
class PhaseRecorder {
    using Clock=std::chrono::steady_clock;
    static constexpr std::size_t none=static_cast<std::size_t>(-1);
    struct Event {
        std::size_t phase=0,input=0,parent=none,result=none;
        std::uint64_t begin=0,end=0;
        int correctness=0;
        bool complete=false;
        std::vector<double> work;
    };
    std::vector<Event> events_;
    struct Phase { std::string name,activity,reason; };
    std::vector<Phase> phases_;
    std::vector<std::string> inputs_,results_;
    struct Counter {std::string name,unit,state;};
    std::vector<Counter> counters_;
    std::size_t used_=0,active_=none;
    Clock::time_point origin_;
    std::uint64_t now() const {
        if (time_source_==TimeSource::LinuxMonotonic) return linux_now()-monotonic_origin_;
        return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-origin_).count());
    }
    static void quoted(std::ostream& out,const std::string& value) {
        const char* hex="0123456789abcdef";
        out<<'"';
        for (unsigned char c:value) {
            if (c=='"' || c=='\\') out<<'\\'<<static_cast<char>(c);
            else if (c<32) out<<"\\u00"<<hex[c>>4]<<hex[c&15];
            else out<<static_cast<char>(c);
        }
        out<<'"';
    }
public:
    enum class TimeSource { SteadyRelative, LinuxMonotonic };
    enum class Activity { Unclassified, Work, Wait };
private:
    TimeSource time_source_=TimeSource::SteadyRelative;
    std::uint64_t monotonic_origin_=0;
    long process_id_=0,thread_id_=0;
    std::string boot_id_,pid_namespace_,time_namespace_;
    static std::uint64_t linux_now() {
#ifdef __linux__
        timespec value;
        if (::clock_gettime(CLOCK_MONOTONIC,&value)!=0) throw std::runtime_error("CLOCK_MONOTONIC unavailable");
        return static_cast<std::uint64_t>(value.tv_sec)*1000000000ULL+static_cast<std::uint64_t>(value.tv_nsec);
#else
        throw std::runtime_error("LinuxMonotonic requires Linux");
#endif
    }
    static std::string namespace_link(const char* path) {
#ifdef __linux__
        char data[128];const auto length=::readlink(path,data,sizeof(data));
        if (length<=0 || static_cast<std::size_t>(length)==sizeof(data)) throw std::runtime_error("Cannot identify Linux clock namespace");
        return std::string(data,static_cast<std::size_t>(length));
#else
        (void)path;throw std::runtime_error("Linux namespace unavailable");
#endif
    }
    static std::string event_id(std::size_t track,std::size_t event,bool grouped) {
        return grouped?std::to_string(track)+":"+std::to_string(event):std::to_string(event);
    }
    void write_stage(std::ostream& out,std::size_t i,const std::string& track,
                     std::size_t track_ordinal,bool grouped,std::uint64_t offset) const {
        const auto& e=events_[i];
        out<<"{\"id\":";quoted(out,event_id(track_ordinal,i,grouped));out<<",\"name\":";quoted(out,phases_[e.phase].name);
        out<<",\"parent_id\":";
        if (e.parent==none) out<<"null";else {out<<'"';if (grouped) out<<track_ordinal<<":";out<<e.parent<<'"';}
        out<<",\"track_id\":";quoted(out,track);out<<",\"call_id\":";quoted(out,event_id(track_ordinal,i,grouped));out<<",\"input_id\":";quoted(out,inputs_[e.input]);
        out<<",\"start_ns\":"<<(offset+e.begin)<<",\"end_ns\":"<<(offset+e.end)
           <<",\"children_complete\":"<<(e.complete?"true":"false")<<",\"result\":";
        quoted(out,e.result==none?"unclassified":results_[e.result]);
        out<<",\"correctness\":\""<<(e.correctness==1?"passed":e.correctness==2?"failed":"not_checked")<<"\",\"work\":{";
        bool first=true;
        for (std::size_t c=0;c<counters_.size();++c) {
            if (std::isnan(e.work[c])) continue;
            if (!first) out<<',';
            first=false;quoted(out,counters_[c].name);out<<":{\"value\":"<<e.work[c]<<",\"unit\":";
            quoted(out,counters_[c].unit);out<<",\"evidence_state\":";quoted(out,counters_[c].state);out<<'}';
        }
        out<<'}';
        const auto& phase=phases_[e.phase];
        if (!phase.activity.empty()) {
            out<<",\"activity\":{\"kind\":";quoted(out,phase.activity);
            if (!phase.reason.empty()) {out<<",\"reason\":";quoted(out,phase.reason);}
            out<<'}';
        }
        out<<'}';
    }
public:
    explicit PhaseRecorder(std::size_t capacity,TimeSource source=TimeSource::SteadyRelative):events_(capacity),origin_(Clock::now()),time_source_(source) {
        if (source==TimeSource::LinuxMonotonic) {
#ifdef __linux__
            process_id_=::getpid();thread_id_=::syscall(SYS_gettid);
            std::ifstream boot("/proc/sys/kernel/random/boot_id");
            if (!(boot>>boot_id_)) throw std::runtime_error("Cannot identify Linux boot clock");
            pid_namespace_=namespace_link("/proc/self/ns/pid");time_namespace_=namespace_link("/proc/self/ns/time");
            monotonic_origin_=linux_now();
#else
            throw std::runtime_error("LinuxMonotonic requires Linux");
#endif
        }
    }
    std::size_t define_phase(const std::string& name) {return define_phase(name,Activity::Unclassified);}
    std::size_t define_phase(const std::string& name,Activity activity,const std::string& reason=std::string()) {
        std::string kind;
        if (activity==Activity::Unclassified) {if (!reason.empty()) throw std::invalid_argument("Unclassified phase cannot have a reason");}
        else if (activity==Activity::Work) {if (!reason.empty()) throw std::invalid_argument("Work phase cannot have a reason");kind="application_work";}
        else if (activity==Activity::Wait) {if (reason.empty()) throw std::invalid_argument("Wait phase requires a reason");kind="application_wait";}
        else throw std::invalid_argument("Unknown activity kind");
        phases_.push_back(Phase{name,kind,reason});return phases_.size()-1;
    }
    std::size_t define_input(const std::string& id) {inputs_.push_back(id);return inputs_.size()-1;}
    std::size_t define_result(const std::string& name) {results_.push_back(name);return results_.size()-1;}
    std::size_t define_counter(const std::string& name,const std::string& unit,const std::string& evidence="Measured") {
        if (evidence!="Measured" && evidence!="Estimated" && evidence!="Modeled") throw std::invalid_argument("Invalid work evidence state");
        for (const auto& counter:counters_) if (counter.name==name) throw std::invalid_argument("Duplicate counter");
        if (used_) throw std::logic_error("Register counters before recording calls");
        counters_.push_back(Counter{name,unit,evidence});
        for (auto& event:events_) event.work.push_back(std::numeric_limits<double>::quiet_NaN());
        return counters_.size()-1;
    }
    void set_work(std::size_t event,std::size_t counter,double value) {
        if (event>=used_ || counter>=counters_.size() || !std::isfinite(value) || value<0) throw std::invalid_argument("Invalid work counter");
        events_[event].work[counter]=value;
    }
    class Scope {
        PhaseRecorder& recorder_;
        std::size_t index_;
    public:
        Scope(PhaseRecorder& recorder,std::size_t phase,std::size_t input,bool children_complete=false):recorder_(recorder),index_(recorder.used_) {
            if (phase>=recorder.phases_.size() || input>=recorder.inputs_.size()) throw std::invalid_argument("Unregistered phase/input ID");
            if (index_==recorder.events_.size()) throw std::length_error("Phase capacity exhausted; no events were silently dropped");
            auto& event=recorder.events_[index_];
            event.phase=phase;event.input=input;event.parent=recorder.active_;event.complete=children_complete;
            ++recorder.used_;recorder.active_=index_;event.begin=recorder.now();
        }
        ~Scope() {
            auto& event=recorder_.events_[index_];event.end=recorder_.now();recorder_.active_=event.parent;
        }
        std::size_t id() const {return index_;}
        Scope(const Scope&)=delete;
        Scope& operator=(const Scope&)=delete;
    };
    void mark(std::size_t event,std::size_t result,bool passed) {
        if (event>=used_ || result>=results_.size()) throw std::invalid_argument("Unknown event/result ID");
        events_[event].result=result;events_[event].correctness=passed?1:2;
    }
    // Sum of inclusive durations, not wall-clock union. Observe after scopes finish.
    std::uint64_t summed_duration_ns() const {
        if (active_!=none) throw std::logic_error("Cannot summarize an unfinished scope");
        std::uint64_t sum=0;
        for (std::size_t i=0;i<used_;++i) sum+=events_[i].end-events_[i].begin;
        return sum;
    }
    void write(const std::string& filename,const std::string& scope) const {
        if (active_!=none) throw std::logic_error("Cannot export an unfinished scope");
        const auto end=now();
        std::ofstream out(filename);
        if (!out) throw std::runtime_error("Cannot create phase report");
        out.precision(17);
        out<<"{\"schema_version\":1,\"scope\":";quoted(out,scope);
        if (time_source_==TimeSource::LinuxMonotonic) {
            out<<",\"clock\":{\"name\":\"CLOCK_MONOTONIC\",\"origin_ns\":"<<monotonic_origin_
               <<",\"tracks\":{\"main\":{\"pid\":"<<process_id_<<",\"tid\":"<<thread_id_<<"}},\"boot_id\":";quoted(out,boot_id_);
            out<<",\"pid_namespace\":";quoted(out,pid_namespace_);out<<",\"time_namespace\":";quoted(out,time_namespace_);out<<'}';
        }
        out<<",\"start_ns\":0,\"end_ns\":"<<end<<",\"stages\":[";
        for (std::size_t i=0;i<used_;++i) {
            if (i) out<<',';
            write_stage(out,i,"main",0,false,0);
        }
        out<<"]}\n";out.close();
        if (!out) throw std::runtime_error("Cannot finish phase report");
    }
    using GroupTrack=std::pair<std::string,std::reference_wrapper<const PhaseRecorder>>;
    static void write_group(const std::string& filename,const std::string& scope,const std::vector<GroupTrack>& tracks) {
        if (tracks.empty()) throw std::invalid_argument("A phase group requires at least one track");
        const auto& first=tracks.front().second.get();
        if (first.time_source_!=TimeSource::LinuxMonotonic) throw std::invalid_argument("Phase groups require LinuxMonotonic recorders");
        std::vector<std::string> names;
        names.reserve(tracks.size());
        std::uint64_t origin=first.monotonic_origin_,end=0;
        for (const auto& item:tracks) {
            const auto& recorder=item.second.get();
            if (item.first.empty() || std::find(names.begin(),names.end(),item.first)!=names.end()) throw std::invalid_argument("Phase track names must be nonempty and unique");
            names.push_back(item.first);
            if (recorder.active_!=none) throw std::logic_error("Cannot export an unfinished scope");
            if (recorder.time_source_!=TimeSource::LinuxMonotonic || recorder.boot_id_!=first.boot_id_ || recorder.pid_namespace_!=first.pid_namespace_ || recorder.time_namespace_!=first.time_namespace_) throw std::invalid_argument("Phase group clocks or namespaces are incompatible");
            origin=std::min(origin,recorder.monotonic_origin_);
            end=std::max(end,recorder.monotonic_origin_+recorder.now());
        }
        std::ofstream out(filename);
        if (!out) throw std::runtime_error("Cannot create phase report");
        out.precision(17);
        out<<"{\"schema_version\":1,\"scope\":";quoted(out,scope);
        out<<",\"clock\":{\"name\":\"CLOCK_MONOTONIC\",\"origin_ns\":"<<origin<<",\"tracks\":{";
        for (std::size_t i=0;i<tracks.size();++i) {
            if (i) out<<',';
            const auto& recorder=tracks[i].second.get();quoted(out,tracks[i].first);
            out<<":{\"pid\":"<<recorder.process_id_<<",\"tid\":"<<recorder.thread_id_<<'}';
        }
        out<<"},\"boot_id\":";quoted(out,first.boot_id_);out<<",\"pid_namespace\":";quoted(out,first.pid_namespace_);
        out<<",\"time_namespace\":";quoted(out,first.time_namespace_);out<<"},\"start_ns\":0,\"end_ns\":"<<(end-origin)<<",\"stages\":[";
        bool comma=false;
        for (std::size_t track=0;track<tracks.size();++track) {
            const auto& recorder=tracks[track].second.get();
            const auto offset=recorder.monotonic_origin_-origin;
            for (std::size_t i=0;i<recorder.used_;++i) {
                if (comma) out<<',';
                comma=true;
                recorder.write_stage(out,i,tracks[track].first,track,true,offset);
            }
        }
        out<<"]}\n";out.close();
        if (!out) throw std::runtime_error("Cannot finish phase report");
    }
};
} // namespace perflab
