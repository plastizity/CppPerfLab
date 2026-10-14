// Independent deterministic fixtures. No CppPerfLab runtime or domain dependency.
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

using U64 = std::uint64_t;

std::size_t number(const char* value) {
    std::size_t end = 0;
    const std::string text(value);
    if (text.empty() || text[0] == '-') throw std::invalid_argument("positive integer required");
    const auto result = std::stoull(text, &end);
    if (end != text.size() || !result || result > 1000000) throw std::invalid_argument("size/repetitions must be 1..1000000");
    return static_cast<std::size_t>(result);
}

__attribute__((noinline)) U64 locality(const std::vector<U64>& values, std::size_t n) {
    U64 sum = 0;
#if CONTIGUOUS_TRAVERSAL
    for (std::size_t row = 0; row < n; ++row)
        for (std::size_t col = 0; col < n; ++col) sum += values[row * n + col];
#else
    for (std::size_t col = 0; col < n; ++col)
        for (std::size_t row = 0; row < n; ++row) sum += values[row * n + col];
#endif
    return sum;
}

__attribute__((noinline)) U64 compute(U64 state, std::size_t n) {
    for (std::size_t i = 0; i < n; ++i) {
        state ^= state >> 13;
        state *= UINT64_C(0x9e3779b97f4a7c15);
        state ^= state << 7;
    }
    return state;
}

int main(int argc, char** argv) {
    try {
        if (argc != 4) throw std::invalid_argument("usage: workload compute|locality|allocations size repetitions");
        const std::string kind(argv[1]);
        const std::size_t n = number(argv[2]), repetitions = number(argv[3]);
        U64 checksum = 0;
        if (kind == "locality") {
            if (n > 8192) throw std::invalid_argument("fixture matrix side exceeds 8192");
            std::vector<U64> values(n * n);
            U64 expected = 0;
            for (std::size_t i = 0; i < values.size(); ++i) { values[i] = i % 251; expected += values[i]; }
            for (std::size_t repeat = 0; repeat < repetitions; ++repeat) {
                values[0] = repeat;
                const U64 result = locality(values, n);
                if (result != expected + repeat) return 2;
                checksum += result;
            }
        } else if (kind == "compute") {
            U64 expected = 42;
            for (std::size_t repeat = 0; repeat < repetitions; ++repeat) {
                // Each transition is checked against two composable half transitions.
                const U64 result = compute(expected, n);
                if (result != compute(compute(expected, n / 2), n - n / 2)) return 2;
                expected = result;
            }
            checksum = expected;
        } else if (kind == "allocations") {
            for (std::size_t repeat = 0; repeat < repetitions; ++repeat) {
                U64 expected = 0;
#if CONTIGUOUS_ALLOCATION
                std::size_t total = 0;
                for (std::size_t i = 0; i < n; ++i) total += 16 + i % 64;
                std::vector<U64> values;
                values.reserve(total);
                for (std::size_t i = 0; i < n; ++i) {
                    const std::size_t count = 16 + i % 64;
                    expected += count * (i + repeat);
                    for (std::size_t j = 0; j < count; ++j) values.push_back(i + repeat);
                }
                U64 result = 0;
                for (auto value : values) result += value;
#else
                std::vector<std::vector<U64>> blocks;
                blocks.reserve(n);
                for (std::size_t i = 0; i < n; ++i) {
                    const std::size_t count = 16 + i % 64;
                    blocks.emplace_back(count, i + repeat);
                    expected += count * (i + repeat);
                }
                U64 result = 0;
                for (const auto& block : blocks) for (auto value : block) result += value;
#endif
                if (result != expected) return 2;
                checksum += result;
            }
        } else throw std::invalid_argument("unknown workload");
        std::cout << "{\"verified\":true,\"checksum\":" << checksum << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
