// Synthetic fixed-work integer reduction; worker count changes only partitioning.
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

static std::uint64_t reduce(std::size_t n, unsigned workers, unsigned repeats) {
    std::vector<std::uint64_t> input(n);
    for (std::size_t i=0;i<n;++i) input[i]=i%1024;
    std::vector<std::uint64_t> partial(workers,0);
    std::vector<std::thread> threads;
    for (unsigned p=0;p<workers;++p) threads.emplace_back([&,p] {
        std::uint64_t sum=0;
        for (unsigned r=0;r<repeats;++r)
            for (std::size_t i=n*p/workers;i<n*(p+1)/workers;++i) sum+=input[i];
        partial[p]=sum;
    });
    for (auto& t:threads) t.join();
    std::uint64_t result=0;
    for (auto value:partial) result+=value;
    return result;
}
static std::uint64_t expected(std::size_t n,unsigned repeats) {
    const auto tail=n%1024;
    return ((n/1024)*523776+tail*(tail-1)/2)*repeats;
}
int main(int argc,char** argv) {
    if (argc==2 && std::string(argv[1])=="--verify") {
        for (auto n:{1u,17u,1024u,1031u,10000u})
            for (auto p:{1u,2u,4u})
                if (reduce(n,p,3)!=expected(n,3)) return 2;
        std::cout<<"exact integer oracle passed\n";return 0;
    }
    if (argc!=4) return 1;
    const auto workers=std::stoul(argv[1]);
    const auto n=std::stoull(argv[2]);
    const auto repeats=std::stoul(argv[3]);
    if (!workers || workers>256 || !n || n>100000000 || !repeats || repeats>1000) return 1;
    const auto result=reduce(n,static_cast<unsigned>(workers),static_cast<unsigned>(repeats));
    if (result!=expected(n,static_cast<unsigned>(repeats))) return 2;
    std::cout<<result<<'\n';
}
