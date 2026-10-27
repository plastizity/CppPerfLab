// Standalone data-layout and access-pattern workload; no business project code.
#include <algorithm>
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

struct Result { std::uint64_t sum; std::uint64_t items; };
struct Link { std::uint64_t value; std::size_t next; };
struct Row { std::uint64_t hot; std::array<std::uint64_t, 7> cold; };

std::uint64_t integer(const char* text, bool allow_zero) {
    const std::string input(text);
    if (input.empty() || input.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument("Expected an unsigned decimal integer");
    std::size_t end = 0;
    const auto value = std::stoull(input, &end);
    if (end != input.size() || (!allow_zero && value == 0))
        throw std::invalid_argument("Invalid integer argument");
    return value;
}

std::uint64_t expected(std::uint64_t size, std::uint64_t rounds) {
    const auto max = std::numeric_limits<std::uint64_t>::max();
    if (size == max || size > std::numeric_limits<std::size_t>::max())
        throw std::invalid_argument("Size is outside the supported range");
    std::uint64_t a = size, b = size + 1;
    if (a % 2 == 0) a /= 2; else b /= 2;
    if (a > max / b || rounds > max / (a * b) || rounds > max / size)
        throw std::invalid_argument("Sum or item count exceeds uint64 range");
    return a * b * rounds;
}

Result calculate(const std::string& mode, std::size_t size, std::uint64_t rounds,
                 std::uint64_t seed) {
    std::uint64_t sum = 0, items = 0;
    if (mode == "seq" || mode == "random") {
        std::vector<Link> nodes(size);
        for (std::size_t i = 0; i < size; ++i) nodes[i] = {i + 1, (i + 1) % size};
        std::size_t start = 0;
        if (mode == "random") {
            std::vector<std::size_t> order(size);
            std::iota(order.begin(), order.end(), 0);
            std::mt19937_64 generator(seed);
            std::shuffle(order.begin(), order.end(), generator);
            for (std::size_t i = 0; i < size; ++i) nodes[order[i]].next = order[(i + 1) % size];
            start = order[0];
        }
        for (std::uint64_t round = 0; round < rounds; ++round) {
            if (mode == "seq") {
                for (std::size_t i = 0; i < size; ++i) { sum += nodes[i].value; ++items; }
            } else {
                auto current = start;
                for (std::size_t i = 0; i < size; ++i) {
                    sum += nodes[current].value; current = nodes[current].next; ++items;
                }
            }
        }
    } else if (mode == "aos") {
        std::vector<Row> rows(size);
        for (std::size_t i = 0; i < size; ++i) {
            rows[i].hot = i + 1;
            rows[i].cold.fill(i);
        }
        for (std::uint64_t round = 0; round < rounds; ++round)
            for (std::size_t i = 0; i < size; ++i) { sum += rows[i].hot; ++items; }
    } else if (mode == "soa") {
        std::vector<std::uint64_t> hot(size);
        std::vector<std::array<std::uint64_t, 7>> cold(size);
        for (std::size_t i = 0; i < size; ++i) { hot[i] = i + 1; cold[i].fill(i); }
        for (std::uint64_t round = 0; round < rounds; ++round)
            for (std::size_t i = 0; i < size; ++i) { sum += hot[i]; ++items; }
    } else {
        throw std::invalid_argument("Unknown mode: expected seq, random, aos or soa");
    }
    return {sum, items};
}

void verify() {
    for (const char* mode : {"seq", "random", "aos", "soa"})
        for (std::size_t size : {std::size_t(1), std::size_t(7), std::size_t(257)})
            for (std::uint64_t rounds : {UINT64_C(1), UINT64_C(3)})
                for (std::uint64_t seed : {UINT64_C(0), UINT64_C(42)}) {
                    const auto result = calculate(mode, size, rounds, seed);
                    if (result.sum != expected(size, rounds) || result.items != size * rounds)
                        throw std::runtime_error("Synthetic verification failed");
                }
}

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--verify") {
            verify();
            std::cout << "synthetic data movement verification passed\n";
            return 0;
        }
        if (argc != 6) throw std::invalid_argument("Expected MODE N ROUNDS SEED REPORT.work.json");
        const auto size = integer(argv[2], false), rounds = integer(argv[3], false), seed = integer(argv[4], true);
        const auto target = expected(size, rounds);
        const auto result = calculate(argv[1], static_cast<std::size_t>(size), rounds, seed);
        if (result.sum != target || result.items != size * rounds)
            throw std::runtime_error("Synthetic result or work count mismatch");
        std::ofstream report(argv[5], std::ios::trunc);
        report << "{\"schema_version\":1,\"definition_id\":\"sum-items-v1\",\"unit\":\"item\","
                  "\"count\":" << result.items << ",\"scope\":\"target-invocation\","
                  "\"count_kind\":\"observed\",\"completed\":true}\n";
        report.close();
        if (!report) throw std::runtime_error("Cannot write work report");
        std::cout << "{\"sum\":" << result.sum << ",\"count\":" << result.items << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
