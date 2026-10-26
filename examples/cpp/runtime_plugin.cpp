// Synthetic origin/candidate libraries deliberately have the same correct result.
#ifndef RUNTIME_FIXTURE_LABEL
#error RUNTIME_FIXTURE_LABEL must identify this synthetic library build
#endif
extern "C" int runtime_fixture_value() { return 7; }
extern "C" const char* runtime_fixture_label() { return RUNTIME_FIXTURE_LABEL; }
