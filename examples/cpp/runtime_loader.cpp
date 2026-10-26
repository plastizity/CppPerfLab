#include <chrono>
#include <dlfcn.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

extern "C" int runtime_fixture_value();
extern "C" const char* runtime_fixture_label();

int main(int argc,char** argv) {
    try {
        if (runtime_fixture_value()!=7) throw std::runtime_error("linked fixture oracle failed");
        if (argc==2 && std::string(argv[1])=="--verify") return 0;
        const bool plugin=argc==4 && std::string(argv[1])=="plugin";
        if (!plugin && !(argc==3 && std::string(argv[1])=="hold")) throw std::invalid_argument("Expected hold MILLISECONDS or plugin FILE MILLISECONDS");
        const std::string count(argv[plugin?3:2]);
        if (count.empty() || count.find_first_not_of("0123456789")!=std::string::npos) throw std::invalid_argument("Invalid milliseconds");
        const auto millis=std::stoul(count);
        void* handle=nullptr;
        if (plugin) {
            handle=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);
            if (!handle) throw std::runtime_error(dlerror());
            const auto function=reinterpret_cast<int(*)()>(dlsym(handle,"runtime_optional_value"));
            if (!function || function()!=9) {dlclose(handle);throw std::runtime_error("optional fixture oracle failed");}
        }
        std::cout << "value=7 library=" << runtime_fixture_label() << '\n';
        std::this_thread::sleep_for(std::chrono::milliseconds(millis));
        if (handle && dlclose(handle)!=0) throw std::runtime_error("dlclose failed");
        return 0;
    } catch (const std::exception& error) {std::cerr << error.what() << '\n';return 1;}
}
