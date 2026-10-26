#include <chrono>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Value=std::uint64_t;
using Clock=std::chrono::steady_clock;

Value parse_count(const std::string& text) {
    std::size_t consumed=0;
    const auto count=std::stoull(text,&consumed,10);
    if (consumed!=text.size() || count==0 || count>std::numeric_limits<Value>::max()/524800ULL)
        throw std::invalid_argument("N must be positive and its closed-form sum must fit uint64");
    if (count>std::numeric_limits<std::size_t>::max() || count>std::vector<Value>().max_size())
        throw std::invalid_argument("N exceeds vector capacity");
    return static_cast<Value>(count);
}

Value closed_form(Value count) {
    const Value cycles=count/1024,remainder=count%1024;
    return cycles*524800ULL+remainder*(remainder+1)/2;
}

Value fill_and_assemble(Value count,std::vector<Value>& values) {
    asm volatile("" : "+r"(count) :: "memory");
    values.resize(static_cast<std::size_t>(count));
    for (Value i=0;i<count;++i) values[static_cast<std::size_t>(i)]=(i&1023ULL)+1;
    Value sum=0;
    for (const auto value:values) sum+=value;
    asm volatile("" : "+r"(sum) :: "memory");
    return sum;
}

void verify() {
    const Value counts[]={1,2,1023,1024,1025,2048,10001};
    for (const auto count:counts) {
        std::vector<Value> values;
        const auto actual=fill_and_assemble(count,values);
        if (values.size()!=count || actual!=closed_form(count))
            throw std::runtime_error("closed-form stage oracle mismatch");
    }
    std::cout<<"correctness=passed verification=closed_form_boundaries\n";
}

double elapsed(const Clock::time_point begin,const Clock::time_point end,double divisor) {
    return static_cast<double>(std::chrono::duration_cast<std::chrono::nanoseconds>(end-begin).count())/divisor;
}
}

int main(int argc,char** argv) {
    try {
        if (argc==2 && std::string(argv[1])=="--verify") {verify();return 0;}
        if (argc!=3) throw std::invalid_argument("usage: logged_stages N format|keywords|bad | --verify");
        const Value count=parse_count(argv[1]);
        const std::string style=argv[2];
        if (style!="format" && style!="keywords" && style!="bad")
            throw std::invalid_argument("STYLE must be format, keywords, or bad");

        std::vector<Value> values;
        const auto model_begin=Clock::now();
        asm volatile("" ::: "memory");
        values.resize(static_cast<std::size_t>(count));
        for (Value i=0;i<count;++i) values[static_cast<std::size_t>(i)]=(i&1023ULL)+1;
        asm volatile("" ::: "memory");
        const auto assemble_begin=Clock::now();
        const Value* data=values.data();std::size_t size=values.size();
        asm volatile("" : "+r"(data),"+r"(size) :: "memory");
        Value sum=0;
        for (std::size_t i=0;i<size;++i) sum+=data[i];
        asm volatile("" : "+r"(sum) :: "memory");
        const auto assemble_end=Clock::now();
        const auto model_end=Clock::now();

        if (sum!=closed_form(count)) throw std::runtime_error("model output failed independent closed-form validation");
        const double model_ms=elapsed(model_begin,model_end,1000000.0);
        const double assemble_ns=elapsed(assemble_begin,assemble_end,1.0);
        const double assemble_us=assemble_ns/1000.0;
        const double own_ms=model_ms-assemble_ns/1000000.0;
        std::cout<<std::setprecision(17);
        if (style=="format") {
            std::cout<<"MODEL total="<<model_ms<<" own="<<own_ms<<" calls=1 items="<<count<<" status=OK\n";
            std::cout<<"ASSEMBLE total="<<assemble_us<<" own="<<assemble_us<<" calls=1 items="<<count<<" status=OK\n";
        } else if (style=="keywords") {
            std::cout<<"[profile] model total_ms:"<<model_ms<<" self_ms:"<<own_ms<<" count:1 items:"<<count<<" check:OK\n";
            std::cout<<"[profile] assemble total_ms:"<<assemble_ns/1000000.0<<" self_ms:"<<assemble_ns/1000000.0<<" count:1 items:"<<count<<" check:OK\n";
        } else {
            std::cout<<"[profile] model total_ms:"<<model_ms<<" self_ms:"<<own_ms
                     <<" total_ms:"<<model_ms<<" count:1 items:"<<count<<" check:OK\n";
            std::cout<<"[profile] assemble total_ms:"<<assemble_ns/1000000.0<<" self_ms:"<<assemble_ns/1000000.0
                     <<" count:1 items:"<<count<<" check:OK\n";
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr<<"logged_stages: "<<error.what()<<'\n';
        return 2;
    }
}
