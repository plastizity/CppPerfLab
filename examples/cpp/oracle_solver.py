#!/usr/bin/env python3.11
"""Independent explicit tridiagonal residual and known-solution error computation.

The platform, not this example, applies configured acceptance thresholds. A well
formed nonconverged result is intentionally reported so the platform can reject it.
"""
import json
import math
from pathlib import Path
import sys

path,n=Path(sys.argv[1]),int(sys.argv[2])
r=json.loads(path.read_text(),parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
x=r['solution']
if len(x)!=n or any(type(v) not in (float,int) or not math.isfinite(v) for v in x):
    raise ValueError('Solution dimension or numeric values invalid')
# Form A*x-b independently; do not trust the solver termination or reported norm.
residual=math.sqrt(math.fsum((4*x[i]-(x[i-1] if i else 0)-(x[i+1] if i+1<n else 0)
                             -(2+int(i==0)+int(i+1==n)))**2 for i in range(n)))
error=math.sqrt(math.fsum((v-1)**2 for v in x))
print(json.dumps(dict(schema_version=1,problem_id='synthetic-spd-1d',residual_norm='l2_absolute',
                      residual=residual,absolute_error=error,relative_error=error/math.sqrt(n)),allow_nan=False))
