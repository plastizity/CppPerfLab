"""Independent semantic oracle. No candidate code or expected-output shortcuts."""
from collections import Counter
import subprocess
import sys

for n in (0, 1, 2, 17, 127, 256, 1024):
    for distribution in ("random", "unique", "equal", "alternating"):
        expected = 0
        for repeat in range(3):
            seed = 12345 + repeat
            values = []
            for i in range(n):
                seed = (seed * 1664525 + 1013904223) & 0xffffffff
                values.append({"random": seed % (n // 4 + 1), "unique": i,
                               "equal": 7, "alternating": i % 2}[distribution])
            expected += sum(count * (count - 1) // 2 for count in Counter(values).values())
        result = subprocess.run([sys.argv[1], str(n), "3", distribution],
                                check=True, stdout=subprocess.PIPE, text=True, timeout=20)
        if int(result.stdout.strip()) != expected:
            raise RuntimeError("Wrong equal-pair count for n={} distribution={}".format(n, distribution))
print("independent oracle passed: 28 cases, 4 distributions, 3 seeds per case")
