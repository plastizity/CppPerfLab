#include "cppperflab/phase.hpp"

#include <condition_variable>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
using Value=std::uint64_t;

Value parse_count(const std::string& text) {
    std::size_t consumed=0;
    const auto value=std::stoull(text,&consumed,10);
    if (consumed!=text.size() || value==0 || value>std::numeric_limits<Value>::max()/2098176ULL)
        throw std::invalid_argument("N must be a positive integer whose reference sum fits uint64");
    return static_cast<Value>(value);
}

__attribute__((noinline)) Value cyclic_sum(Value count,Value period) {
    asm volatile("" : "+r"(count) :: "memory");
    Value sum=0;
    for (Value i=0;i<count;++i) sum+=(i%period)+1;
    asm volatile("" : "+r"(sum) :: "memory");
    return sum;
}

Value closed_form(Value count,Value period) {
    const Value cycles=count/period,remainder=count%period;
    return cycles*(period*(period+1)/2)+remainder*(remainder+1)/2;
}

void verify() {
    const Value counts[]={1,17,1023,1024,1025,2048,10001};
    const Value periods[]={1024,2048};
    for (const auto count:counts) for (const auto period:periods)
        if (cyclic_sum(count,period)!=closed_form(count,period)) throw std::runtime_error("closed-form oracle mismatch");
    std::cout<<"{\"correctness\":\"passed\",\"verification\":\"boundary_oracle\"}\n";
}
}

int main(int argc,char** argv) {
    try {
        if (argc==2 && std::string(argv[1])=="--verify") {verify();return 0;}
        if (argc!=3) throw std::invalid_argument("usage: thread_activity N REPORT | --verify");
        const Value count=parse_count(argv[1]);
        perflab::PhaseRecorder controller(1,perflab::PhaseRecorder::TimeSource::LinuxMonotonic);
        const auto controller_phase=controller.define_phase("controller_compute",perflab::PhaseRecorder::Activity::Work);
        const auto controller_input=controller.define_input("cyclic-sum-N="+std::to_string(count));
        const auto controller_result=controller.define_result("closed_form_match");

        std::unique_ptr<perflab::PhaseRecorder> worker;
        std::size_t worker_wait_event=0,worker_compute_event=0;
        Value worker_value=0;
        std::mutex mutex;
        std::condition_variable condition;
        bool waiting=false,release=false;
        std::thread worker_thread([&] {
            worker.reset(new perflab::PhaseRecorder(2,perflab::PhaseRecorder::TimeSource::LinuxMonotonic));
            const auto wait_phase=worker->define_phase("worker_condition_wait",perflab::PhaseRecorder::Activity::Wait,"synchronization");
            const auto compute_phase=worker->define_phase("worker_compute",perflab::PhaseRecorder::Activity::Work);
            const auto input=worker->define_input("cyclic-sum-N="+std::to_string(count));
            const auto wait_result=worker->define_result("synchronization_released");
            const auto compute_result=worker->define_result("closed_form_match");
            std::unique_lock<std::mutex> lock(mutex);
            waiting=true;
            condition.notify_one();
            {
                perflab::PhaseRecorder::Scope scope(*worker,wait_phase,input);
                worker_wait_event=scope.id();
                condition.wait(lock,[&] {return release;});
            }
            lock.unlock();
            {
                perflab::PhaseRecorder::Scope scope(*worker,compute_phase,input);
                worker_compute_event=scope.id();
                worker_value=cyclic_sum(count,2048);
            }
            worker->mark(worker_compute_event,compute_result,worker_value==closed_form(count,2048));
            worker->mark(worker_wait_event,wait_result,true);
        });

        {
            std::unique_lock<std::mutex> lock(mutex);
            condition.wait(lock,[&] {return waiting;});
        }
        Value controller_value=0;
        {
            perflab::PhaseRecorder::Scope scope(controller,controller_phase,controller_input);
            controller_value=cyclic_sum(count,1024);
        }
        controller.mark(0,controller_result,controller_value==closed_form(count,1024));
        {
            std::lock_guard<std::mutex> lock(mutex);
            release=true;
        }
        condition.notify_one();
        worker_thread.join();

        if (controller_value!=closed_form(count,1024) || worker_value!=closed_form(count,2048))
            throw std::runtime_error("workload correctness failure");
        perflab::PhaseRecorder::write_group(argv[2],"synthetic_two_thread_activity",{
            {"controller",std::cref(controller)},
            {"worker",std::cref(*worker)}
        });
        std::cout<<"{\"correctness\":\"passed\",\"input_id\":\"cyclic-sum-N="<<count
                 <<"\",\"classification_scope\":\"application phase; includes wakeup and scheduling return cost; not off-CPU proof\"}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr<<"thread_activity: "<<error.what()<<'\n';
        return 2;
    }
}
