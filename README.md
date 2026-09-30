# Coil Efficiency Bench

[![tests](https://github.com/sparkainlp-x/coil-efficiency-bench/actions/workflows/tests.yml/badge.svg)](https://github.com/sparkainlp-x/coil-efficiency-bench/actions/workflows/tests.yml)

An offline Python utility for checking whether a proposed motor coil-winding or topology change is associated with an efficiency difference in **paired, same-load measurements**. It is a data-analysis utility—not a physics simulator, motor controller, or measurement-collection system. It uses only the Python standard library and makes no network or hardware calls.

The included `data/synthetic_measurements.csv` is **synthetic illustrative data** created only to demonstrate the format and calculations. It is not measured data and must never be presented or interpreted as experimental evidence. When the bundled sample, or an unmodified copy of its readings, is analyzed, the tool labels its output as synthetic. An edited copy is not recognized, so label any derived files yourself.

## Run it

Requires Python 3.10 or newer; no installation or third-party packages are needed.

```bash
python3 coil_bench.py data/synthetic_measurements.csv --threshold-pp 2.0
python3 coil_bench.py data/synthetic_measurements.csv --threshold-pp 2.0 --json
python3 -m unittest discover -s tests -v
python3 -m py_compile coil_bench.py tests/test_coil_bench.py
```

`--threshold-pp` is required so a minimum practically meaningful efficiency gain is stated explicitly. The `2.0` value above is only an example chosen for the synthetic demonstration, not a recommended engineering threshold. Set and record the threshold before collecting or inspecting the comparison data; do not change it after seeing the result. For the sample, the output includes a synthetic-data notice. Replace the sample with your own valid CSV to analyze measurements; results are descriptive and apply **only to the specific tested hardware and conditions**. This project does not test broader claims about gravity, unified-field ideas, vertical lift/take-off, or cosmic processes.

## Measurement CSV

Use exactly this header (column order may vary; surrounding spaces and a UTF-8 byte-order mark are tolerated):

```csv
pair_id,configuration,load_nm,input_power_w,shaft_torque_nm,speed_rpm
```

Each `pair_id` must have exactly one `baseline` and one `candidate` row. The two rows must have the same `load_nm` value (within 1e-9 Nm). Use a stable identifier for one matched test occasion/load point; use multiple unique IDs for repeat pairs. At least two complete pairs are required. All numeric fields must be finite, plain decimal numbers (for example `200`, `0.35`, or `2.0e2`); forms such as `nan`, `inf`, `1_000`, or non-ASCII digits are rejected. Load, torque, and speed must be non-negative, and input power must be positive. Rows that imply efficiency above 100% are rejected as likely unit or reading errors.

- `load_nm`: measured or controlled applied-load setting used to match the two runs in the pair.
- `input_power_w`: measured real electrical input power in watts. For AC motors, use a suitable true-power measurement rather than assuming voltage times current is real power.
- `shaft_torque_nm`: measured shaft torque in newton-metres.
- `speed_rpm`: measured shaft speed in revolutions per minute.

The tool calculates mechanical output power as `torque × 2π × rpm / 60`, then efficiency as `mechanical output power / measured input power`. Each pair's change is candidate efficiency minus baseline efficiency in **percentage points**. The report summarizes the average of those within-pair changes, their sample standard deviation and standard error, and a two-sided 95% confidence interval (using the Student's t critical value with n − 1 degrees of freedom for any number of pairs). If every pair shows the same change (zero spread), no interval or threshold verdict is reported, because a zero-width interval would overstate certainty. The interval is assessed against the required pre-specified minimum gain: the report says whether the entire interval is above, below, or overlaps that threshold. An interval below the threshold is not proof of no effect or equivalence.

## Interpreting a comparison

Pairing controls the comparison for the recorded load point, but it does not remove other confounders. For a useful test, keep the motor, drive, cooling, instrumentation, warm-up, and operating procedure consistent; calibrate instruments; document uncertainty and units; and repeat the paired runs. If possible, alternate or randomize baseline/candidate order to reduce drift effects. Treat a confidence interval that includes zero as inconclusive evidence of a consistent directional change; even an interval excluding zero is not proof that a winding change alone caused the difference or that the result generalizes.

The software validates the CSV structure, finite values, complete pairs, and matching load settings. Invalid input exits with status 2 and an error message on standard error; nothing is reported for a partially valid file. It cannot validate calibration, measurement technique, or whether a supplied file is genuinely experimental. Do not label synthetic or otherwise unverified inputs as experimental evidence.

## Repository contents

- `coil_bench.py` — analysis CLI and validation logic.
- `data/synthetic_measurements.csv` — clearly identified synthetic demonstration data.
- `tests/test_coil_bench.py` — standard-library unit tests.
- `LICENSE`, `COMMERCIAL-LICENSE.md`, `CITATION.cff` — licensing and citation metadata.

## Citation

See [CITATION.cff](CITATION.cff).

## License

This software is available under the GNU Affero General Public License v3.0 only (AGPL-3.0-only); see [LICENSE](LICENSE).

Organizations that want to use it in proprietary products or services without AGPL obligations can contact the author about a commercial license via https://sparkainlpx.xyz.
