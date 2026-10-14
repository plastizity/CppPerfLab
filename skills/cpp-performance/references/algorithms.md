# Algorithm experiments without a second measurement system

A study takes one ordinary experiment template and a bounded variant × scale matrix.
`axis.parameter` selects a workload parameter; `axis.argument_index` selects its
argv position (0 is prohibited). Values are positive feature sizes. Other features
(distribution, precision, repeats and threads) stay fixed. For other distributions
or thread configurations, use separate studies and keep all evidence.

Each variant declares an executable, revision and build artifacts. The independent
oracle argv contains `{candidate}`; the study substitutes only that path. Do not
use the candidate's own success flag as the sole proof for a replacement algorithm.
The integer pair-count example uses a Python Counter oracle over edge sizes, four
distributions and three seeds. That is an example of semantics, not a domain rule.

Study results under `.perflab/studies/STUDY_ID/study.json` index ordinary runs. All
metrics, identity, controls and raw data live in those same experiment records.
`budget` bounds experiment count, each measurement has a timeout/resource limits,
and `seed` fixes randomized job order. This is finite variant evaluation; it is
not pyATF, an evolutionary engine, or a production runtime selection system.

The growth fitter compares constant, log N, N, N log N, N² and N³ transforms with
nonnegative slope/intercept and leave-one-out error. Read all fits, raw medians,
CV, scale range and local log slopes. At least four points are required; five or
more with sufficient duration/repetitions are preferable. Startup dominates short
processes; batch more genuine operations in the target, using identical batches
and observable output for every candidate. Do not remove setup or oracle work
from one candidate only. Fitting does not establish asymptotic Big-O.

Algorithm-selection evidence is per scale and tested conditions. A candidate may
win at one size and lose at another; retain that tradeoff. Do not install an input
threshold or auto-switch in production from one small study. Memory studies use
their own adapter and metric; do not merge their instrumentation timing into native
timing studies. Multiobjective acceptance and mature parameter search remain an
external Agent decision until an appropriate adapter is validated.

## Multiple metrics from one scale matrix

Set optional `secondary_metrics` in study v1 to analyze additional collected
quantities with the same run IDs. For example a DHAT study can use
`metric: memory.heap_peak_live` and secondary `memory.allocations` and
`memory.allocated_bytes`. All must be collected and Measured; no missing quantity
is filled in and no additional evaluator or run schedule is created.

`growth` remains the primary series; `secondary_growth` stores the extra series.
Each fit includes structured point-quality screening and the recorded n/CV policy.
Growth ratios and local log slopes are Estimated; candidate model predictions are
Modeled. A quality PASS establishes neither Big-O nor unique model identification.
Queries and HTML separate metric/unit/method/scope series. Multi-parameter regression
and automatic algorithm dispatch remain outside this implementation.
