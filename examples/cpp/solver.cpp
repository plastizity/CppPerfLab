// Reviewed synthetic SPD tridiagonal solve; all numeric assumptions stay in examples.
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>
using Clock=std::chrono::steady_clock;
static double elapsed(Clock::time_point a,Clock::time_point b) {return std::chrono::duration<double>(b-a).count();}
static double rhs(std::size_t i,std::size_t n) {return 2.0+(i==0?1.0:0.0)+(i+1==n?1.0:0.0);}
static double residual(const std::vector<double>& x) {
    double square=0;
    for (std::size_t i=0;i<x.size();++i) {
        const double r=4*x[i]-(i?x[i-1]:0)-(i+1<x.size()?x[i+1]:0)-rhs(i,x.size());
        square+=r*r;
    }
    return std::sqrt(square);
}
static std::vector<double> solve(std::size_t n,double tolerance,unsigned maximum,std::vector<double>& history) {
    std::vector<double> x(n,0),next(n,0);
    history.push_back(residual(x));
    for (unsigned k=0;k<maximum && history.back()>tolerance;++k) {
        for (std::size_t i=0;i<n;++i) {
            const double value=(rhs(i,n)+(i?x[i-1]:0)+(i+1<n?x[i+1]:0))/4;
#if SOLVER_GS
            x[i]=value;
#else
            next[i]=value;
#endif
        }
#if !SOLVER_GS
        x.swap(next);
#endif
        history.push_back(residual(x));
    }
    return x;
}
int main(int argc,char** argv) {
    if (argc==2 && std::string(argv[1])=="--verify") {
        for (auto n:{1u,2u,17u,63u}) {
            std::vector<double> history;
            const auto x=solve(n,1e-12,200,history);
            if (history.back()>1e-12) return 2;
            for (auto value:x) if (std::abs(value-1)>1e-11) return 2;
        }
        std::cout<<"synthetic SPD solver oracle passed\n";return 0;
    }
    if (argc!=5) return 1;
    const auto n=std::stoull(argv[1]);const double tolerance=std::stod(argv[2]);
    const auto maximum=std::stoul(argv[3]);
    if (!n || n>1000000 || !std::isfinite(tolerance) || tolerance<=0 || !maximum || maximum>10000) return 1;
    const auto begin=Clock::now();
    std::vector<double> history;history.reserve(maximum+1);
    const auto ready=Clock::now();
    const auto x=solve(n,tolerance,static_cast<unsigned>(maximum),history);
    const auto finished=Clock::now();
    std::ofstream output(argv[4]);
    if (!output) return 1;
    output<<std::setprecision(17)<<"{\"schema_version\":1,\"problem_id\":\"synthetic-spd-1d\",\"termination\":\""
          <<(history.back()<=tolerance?"converged":"max_iterations")<<"\",\"iterations\":"<<history.size()-1
          <<",\"residual_norm\":\"l2_absolute\",\"setup_seconds\":"<<elapsed(begin,ready)
          <<",\"solve_seconds\":"<<elapsed(ready,finished)<<",\"total_seconds\":"<<elapsed(begin,finished)
          <<",\"residual_history\":[";
    for (std::size_t i=0;i<history.size();++i) output<<(i?",":"")<<history[i];
    output<<"],\"solution\":[";
    for (std::size_t i=0;i<x.size();++i) output<<(i?",":"")<<x[i];
    output<<"]}\n";
    output.close();return output?0:1;
}
