// Algorithm comparison fixture: count equal unordered pairs exactly.
// Baseline enumerates pairs; candidate sorts then counts equal runs.
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

std::vector<std::uint32_t> input(std::size_t n, std::uint32_t seed, const std::string& distribution) {
    std::vector<std::uint32_t> values(n);
    for (std::size_t i = 0; i < n; ++i) {
        seed = seed * 1664525u + 1013904223u;
        if (distribution == "random") values[i] = seed % (n / 4 + 1);
        else if (distribution == "unique") values[i] = static_cast<std::uint32_t>(i);
        else if (distribution == "equal") values[i] = 7;
        else if (distribution == "alternating") values[i] = i % 2;
        else throw std::invalid_argument("unknown distribution");
    }
    return values;
}

__attribute__((noinline)) std::uint64_t pairs(std::vector<std::uint32_t>& values) {
    std::uint64_t result = 0;
#if SORT_PAIRS
    std::sort(values.begin(), values.end());
    std::size_t begin = 0;
    while (begin < values.size()) {
        std::size_t end = begin + 1;
        while (end < values.size() && values[end] == values[begin]) ++end;
        const std::uint64_t count = end - begin;
        result += count * (count - 1) / 2;
        begin = end;
    }
#else
    for (std::size_t i = 0; i < values.size(); ++i)
        for (std::size_t j = i + 1; j < values.size(); ++j)
            result += values[i] == values[j];
#endif
    return result;
}

#ifndef PAIRS_NO_MAIN
int main(int argc, char** argv) {
    try {
        if (argc != 4) throw std::invalid_argument("usage: duplicate_counter N repetitions distribution");
        const auto n = std::stoull(argv[1]), repeats = std::stoull(argv[2]);
        if (n > 1000000 || repeats < 1 || repeats > 1000000) throw std::invalid_argument("invalid fixture size");
        std::uint64_t checksum = 0;
        for (std::size_t repeat = 0; repeat < repeats; ++repeat) {
            auto values = input(n, static_cast<std::uint32_t>(12345 + repeat), argv[3]);
            checksum += pairs(values);
        }
        std::cout << checksum << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
#endif
