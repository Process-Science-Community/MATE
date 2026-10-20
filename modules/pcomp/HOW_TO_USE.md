# How to use Pcomp

Pcomp tests whether two event logs differ by more than chance. You pick a second log, run a permutation test or a bootstrap test, and get a p-value for the difference between the traces of the two logs.

## When to use it

- You have two logs, such as two departments, two periods or the data before and after a change, and you want to know whether they behave differently instead of judging two process maps by eye.
- You want to know what differs, not only whether something does. Pcomp returns one p-value and no picture. For the overlaid process map, the variant differences and the per-activity changes, use Process Comparison.
- You want to compare speed. Pcomp does not register waiting or processing times in MATE, so compare the Performance figures of both logs instead.

> [!WARNING] In this version both tests compare activity sequences only. The permutation test is the timed variant, but MATE does not hand it start timestamps, so every event counts as instantaneous. Two logs with the same traces and very different timing show no difference.

## Before you start

- The log you open the module on is the baseline. It must be case-centric, with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- You need a second log in your account that is ready and case-centric. Object-centric logs and the baseline itself are not offered. If there is none, the panel says "No other ready case-centric logs available. Import a second log first."
- Nothing is precomputed. A test runs only when you start it, as a background job with a progress bar. This page has no filter bar.
- Each log is used as it stands. A filter applied on a log's **Events** tab narrows that log in the comparison, because an applied filter is active across all modules.

## Settings

### Log picker

| Setting | Default | What it changes |
| --- | --- | --- |
| **Compare baseline against** | No log selected | The second log. Each entry shows the log's name and its number of cases. The two test tabs appear once you have picked one. |

### Permutation Test

Start it with **Run permutation test**.

| Setting | Default | What it changes |
| --- | --- | --- |
| **Distribution size** | 1000 | How many random permutations make up the reference distribution. The field is set up for 100 to 20000 in steps of 100; typed values are not checked. More permutations give a more precise p-value and take longer. |
| **Seed** | 42 | Seeds the random sampling (0 or higher). The same seed on the same logs repeats a run; another seed shows how far the p-value moves with the sampling. |
| **Weighted time cost** | On | Divides the time part of the trace distance by the number of time bins, so that control flow and time count equally. On the logs MATE imports, which have no service times, it only rescales the distances by a constant factor and leaves the p-value unchanged. |

### Bootstrap Test

Start it with **Run bootstrap test**.

| Setting | Default | What it changes |
| --- | --- | --- |
| **Bootstrap dist size** | 1000 | How many resampled distances make up the reference distribution. The field is set up for 100 to 20000 in steps of 100. More resamples give a more stable p-value and take longer. |
| **Resample size (0.1 – 1.0)** | 1.0 | The size of each resample as a share of the baseline's cases, in steps of 0.1. At 1.0 a resample has as many cases as the baseline. |
| **Seed** | 42 | As for the permutation test. |

Each button is disabled while the test on its tab runs.

## Reading the results

### The result card

When a run finishes, a card titled "Permutation Test Result" or "Bootstrap Test Result" shows the p-value with four decimals, for example `p = 0.0123`. It is red below 0.01, amber below 0.05 and green from 0.05 up, and a badge puts it into words:

- p below 0.01: "Highly significant difference (p < 0.01)".
- p below 0.05: "Significant difference (p < 0.05)".
- p below 0.10: "Marginal difference (p < 0.10)".
- Otherwise: "No significant difference (p ≥ 0.10)".

Below it the card lists the settings the run used: Distribution size, Seed and Weighted time cost for the permutation test; Bootstrap dist size, Resample size and Seed for the bootstrap test. It shows no distance and no plot of the reference distribution.

A small p-value means the distance between the two logs is larger than nearly every distance the test produces by chance, so the difference is unlikely to be random. A large p-value only says that no difference was detected; it does not prove the logs are the same. The two tests build different reference distributions, so their p-values are not interchangeable.

The card shows the run you started in this visit. The result of the latest run of each test against each second log is stored, and running the same test against the same log again replaces it, but the page does not show earlier results when you reopen it.

### Permutation Test

The test pools the cases of both logs, splits them at random into two groups of the original sizes **Distribution size** times and measures the distance each time. The p-value is the share of those distances that are larger than the distance between your two logs. It treats the two logs alike, so swapping them changes the p-value at most slightly.

### Bootstrap Test

The panel calls it the P-P-UP test for control-flow similarity: it asks whether the two logs could come from the same unknown process. Its reference distribution comes from the baseline alone. **Bootstrap dist size** times it draws a resample of the baseline's cases, with replacement, and measures its distance to the baseline. The p-value is the share of those distances that are at least as large as the distance between the baseline and the second log. Because only the baseline is resampled, swapping the two logs can change the p-value.

## Tips and limits

- Start with the defaults. If the p-value lands near a threshold such as 0.05, raise the size of the distribution and run again for a firmer value. A p-value of 0.0000 means that none of the sampled distances reached the observed one.
- Only activities and their order count. Other columns, such as the resource, are not used, and neither is any timing. To compare speed, open Performance for each log.
- A significant result is not necessarily an important one: with large logs a small difference gets a tiny p-value, and with small logs a real difference can be missed. Use Process Comparison to see what differs and by how much.
- Both tests first compute the distance between every pair of distinct traces, so run time and memory grow with the square of the number of distinct traces, more than with the number of events. The sampling then grows with the distribution size.
- The progress bar stays empty while a test runs. The line under it names the current step, such as "Running permutation test" or "Running bootstrap test", until the test finishes.
- If a run cannot start, the browser shows an alert with the reason. If it fails while running, the reason appears under the button.

## Method

Pcomp wraps the hypothesis test of Pitsch et al., Hypothesis Testing for Processes (2025). Each log is described as a distribution over its traces, and the distance between two logs is the Earth Mover's Distance: the least total effort needed to turn one distribution into the other, where moving weight between two traces costs the distance between those traces. Traces are compared with a Levenshtein distance that counts inserted, deleted and renamed activities and is divided by the length of the longer trace. The timed variant used by the permutation test describes every trace by its activities and the binned service time of each event, the time from an event's start to its timestamp. Because MATE passes no start timestamps, that time is zero for every event.

The p-value is then the share of reference distances that exceed the observed distance: strictly larger for the permutation test, at least as large for the bootstrap test. The formal citations are in the info popover next to the module name.
