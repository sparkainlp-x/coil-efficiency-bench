# Coil Efficiency Bench

[![tests](https://github.com/sparkainlp-x/coil-efficiency-bench/actions/workflows/tests.yml/badge.svg)](https://github.com/sparkainlp-x/coil-efficiency-bench/actions/workflows/tests.yml)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23067995.svg)](https://doi.org/10.5281/zenodo.23067995)

An offline Python utility for checking whether a proposed motor coil-winding or topology change is associated with an efficiency difference in **paired, same-load measurements**. It is a data-analysis utility—not a physics simulator, motor controller, or measurement-collection system. It uses only the Python standard library and makes no network or hardware calls.

The included `data/synthetic_measurements.csv` is **synthetic** data that only demonstrates the format; it is not experimental evidence. Output for the sample, or an unmodified copy of its readings, is labelled synthetic; an edited copy is not recognized, so label derived files yourself.

## Install and run

Requires Python 3.10 or newer and no third-party packages. Run the script from this directory, or install it from a clone to get a `coil-bench` command (usable in place of `python3 coil_bench.py`):

```bash
python3 -m pip install .
coil-bench --version
```

```bash
python3 coil_bench.py data/synthetic_measurements.csv --threshold-pp 2.0
python3 coil_bench.py data/synthetic_measurements.csv --threshold-pp 2.0 --json
python3 -m unittest discover -s tests -v
python3 -m py_compile coil_bench.py tests/test_coil_bench.py
```

`--threshold-pp` (required) is the minimum efficiency gain, in percentage points, that you consider practically meaningful. `2.0` is just a demo value, not a recommendation. Fix and record it before collecting or looking at the data. Results describe **only the tested hardware and conditions**.

## Measurement CSV

Use exactly this header (column order may vary; surrounding spaces and a UTF-8 byte-order mark are tolerated):

```csv
pair_id,configuration,load_nm,input_power_w,shaft_torque_nm,speed_rpm
```

Each `pair_id` must have exactly one `baseline` and one `candidate` row. The two rows must have the same `load_nm` value (within 1e-9 Nm). Use a stable identifier for one matched test occasion/load point; use multiple unique IDs for repeat pairs. At least two complete pairs are required. All numeric fields must be finite, plain decimal numbers (for example `200`, `0.35`, or `2.0e2`); forms such as `nan`, `inf`, `1_000`, or non-ASCII digits are rejected. Load, torque, and speed must be non-negative, and input power must be positive. Rows that imply efficiency above 100% are rejected as likely unit or reading errors.

- `load_nm`: the load **set-point** for the run (for example the dynamometer or brake torque command), in newton-metres. It is used only to check that the two runs of a pair were made at the same load point; it does **not** enter the efficiency calculation.
- `input_power_w`: measured real electrical input power in watts. For AC motors, use a suitable true-power measurement rather than assuming voltage times current is real power.
- `shaft_torque_nm`: the shaft torque actually **measured** during the run, in newton-metres. Together with speed it determines mechanical output power. It can differ from `load_nm` (controller error, friction or where the torque is sensed), which is why both are recorded. The tool does not require the two to agree, because the acceptable gap depends on the rig; review large differences yourself. In the synthetic sample they are equal by construction.
- `speed_rpm`: measured shaft speed in revolutions per minute.

The tool calculates mechanical output power as `torque × 2π × rpm / 60`, then efficiency as `mechanical output power / measured input power`. Each pair's change is candidate efficiency minus baseline efficiency in **percentage points**. The report summarizes the average of those within-pair changes, their sample standard deviation and standard error, and a two-sided 95% confidence interval (using the Student's t critical value with n − 1 degrees of freedom for any number of pairs). If every pair shows the same change (zero spread), no interval or threshold verdict is reported, because a zero-width interval would overstate certainty. This is the standard paired t-test interval: it matches `scipy.stats.ttest_rel(candidate, baseline).confidence_interval(0.95)` to within 1e-6 percentage points (checked by a test that runs when SciPy is installed; SciPy is not needed to use the tool). The interval is assessed against the required pre-specified minimum gain: the report says whether the entire interval is above, below, or overlaps that threshold. An interval below the threshold is not proof of no effect or equivalence.

## Interpreting a comparison

Pairing controls for the recorded load point, not for other confounders. Keep the motor, drive, cooling, instrumentation, warm-up and procedure consistent; calibrate instruments; repeat pairs; and alternate or randomize baseline/candidate order to limit drift. An interval that includes zero is inconclusive, an interval below the threshold is not proof of no effect, and an interval above it does not show that the winding change alone caused the difference.

Invalid input exits with status 2 and a message on standard error; nothing is reported for a partially valid file. The tool cannot check calibration, measurement technique, or whether data are genuinely experimental.

**Test standards.** How to measure motor efficiency (loss segregation, temperature correction, instrument accuracy, load points) is standardized in **IEC 60034-2-1** (rotating electrical machines) and **IEEE Std 112** (polyphase induction motors). This tool does not implement those procedures and makes no claim of compliance with them; it only summarizes paired values you supply. Collect the input according to the standard that applies to your machine.

## Related work / when to use something else

- **General statistics:** `scipy.stats.ttest_rel`, R's `t.test(..., paired = TRUE)`, or [pingouin](https://pingouin-stats.org/) give the same paired t interval and more (effect sizes, non-parametric alternatives such as the Wilcoxon signed-rank test, equivalence tests via TOST). Use them if your data are not a clean baseline/candidate CSV or you need other analyses.
- **Mixed load points or many factors:** a regression or mixed-effects model (e.g. statsmodels) can model load and run order explicitly instead of pooling pairs.
- **Measurement uncertainty:** the [GUM](https://www.bipm.org/en/committees/jc/jcgm/publications) approach (JCGM 100) and tools such as the Python `uncertainties` package propagate instrument uncertainty, which this tool does not.

Coil Efficiency Bench is useful as a small, dependency-free, auditable check with strict CSV validation and a pre-registered threshold for a simple paired baseline/candidate comparison.

## Repository contents

- `coil_bench.py` — analysis CLI and validation logic.
- `pyproject.toml` — packaging (`pip install .`); the version lives in `coil_bench.__version__`.
- `data/synthetic_measurements.csv` — clearly identified synthetic demonstration data.
- `tests/test_coil_bench.py` — standard-library unit tests.
- `LICENSE`, `COMMERCIAL-LICENSE.md`, `CITATION.cff` — licensing and citation metadata.

## Citation

See [CITATION.cff](CITATION.cff).

## License

This software is available under the GNU Affero General Public License v3.0 only (AGPL-3.0-only); see [LICENSE](LICENSE).

Organizations that want to use it in proprietary products or services without AGPL obligations can contact the author about a commercial license via https://sparkainlpx.xyz.
