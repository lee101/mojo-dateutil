# Parallelism measurements

The prompt for the parallel pass pointed at this file; it did not exist in this
repo before that pass, so it records only what was measured for it. Raw numbers
are on this host: Intel Xeon E5-2697 v4 at 2.30 GHz, 72 CPUs visible, shared
with other benchmark runs and other agents. Python 3.13.14 in the pixi env,
numpy 2.5.1, python-dateutil 2.9.0.post0.

## 1. Fixed cost of one `parallelize` launch

`max.algorithm.parallelize`, warm, from a built binary on this toolchain
(`mojo ==1.2.0.dev2026092605`):

| workers | launch cost |
| ---: | ---: |
| 1 | ~0.06 us |
| 4 | ~20 us (first call in a process: ~594 us) |

From a ctypes host the explicit-count form works; the notes' warning about the
2-argument form was not revisited here. `worker=1` therefore routes to the serial
body rather than launching one worker thread.

## 2. Where the crossover is

Kernel builder: `MD_SRC`/`MD_OUT` (see `build/build.sh`) with
`PARSE_PARALLEL_THRESHOLD = 1`, so every size takes the split path and the
crossover is visible instead of being hidden by the gate. Alternating median
rounds, because whichever call is measured second otherwise looks ~1.5 ms
faster at small sizes.

| records | 4 vs serial |
| --- | ---: |
| 16,384 | 1.01x |
| 49,152 | 1.03x |
| 65,536 | 1.01x |
| 98,304 | 0.72x |
| 131,072 | 0.88x |
| 196,608 | 1.24x |
| 262,144 | 1.75x |

Crossover between 131k and 197k records. 98k is the clearest loss (0.72x) and
131k still loses (0.88x). Two earlier runs of the same sweep gave 0.76x and
1.03x at those two points, so the exact crossover moves with host load; it is
somewhere in that band, not a sharp edge. 524,288 clears it.

## 3. Shipped threshold and worker counts

Shipped library, `PARSE_PARALLEL_THRESHOLD = 524288`, alternating medians.
`262,144` is below the threshold and runs the serial body by construction.

| records | serial | 1 | 2 | 4 | 8 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 262,144 | 23.38 | 23.69 | 23.84 | 25.40 | 25.20 |
| 524,288 | 46.94 | 43.44 | 35.60 | 22.06 | 22.30 |
| 1,000,000 | 84.93 | 80.23 | 62.97 | 36.57 | 37.96 |
| 2,000,000 | 182.46 | 163.53 | 102.21 | 64.98 | 66.69 |
| 4,000,000 | 344.48 | 341.08 | 216.94 | 116.79 | 120.04 |

All values ms. 4 workers reach 2.13x at 524k and 2.95x at 4M; 8 is within 4% of
4 either way, so the cap stays at 4 on a shared host.

## 4. Why `parse_many` end-to-end does not move

`mdu_parse_iso_many` costs ~105 ns/record; at 100k records that is ~10 ms out of
a ~155 ms `parse_many` call. The rest is encoding (`~14 ms`), buffer setup, and
`datetime` materialization (`~100 ms`). The old design also got some overlap
for free: its ctypes calls release the GIL, so the native chunks ran while
Python kept encoding. That is why replacing four threaded serial calls with one
parallel native call came out at parity (0.96x-1.04x) rather than the 2x the
kernel shows on its own.

## 5. `rrule` is not a candidate

250,000 SECONDLY occurrences: native generator 0.8 ms, `datetime`
materialization ~400 ms. Splitting 0.8 ms cannot pay a 20 us launch, and the
dominant Python work is not parallelisable from Mojo.
