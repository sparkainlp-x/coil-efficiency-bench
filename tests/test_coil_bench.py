# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
import ast
import contextlib
import csv
import dataclasses
import io
import json
import math
import re
import shutil
import statistics
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coil_bench  # noqa: E402

try:  # Optional: only used to cross-check the paired t interval.
    from scipy import stats as scipy_stats
except ImportError:  # pragma: no cover - SciPy is not a dependency
    scipy_stats = None

SAMPLE = ROOT / "data" / "synthetic_measurements.csv"


HEADER = ",".join(coil_bench.REQUIRED_COLUMNS)


def csv_text(rows, header=HEADER):
    return header + "\n" + "\n".join(rows) + "\n"


def complete_rows(candidate_power="190"):
    rows = []
    for pair_id, speed in (("A", "3000"), ("B", "2980")):
        rows.append(f"{pair_id},baseline,0.4,200,0.4,{speed}")
        rows.append(f"{pair_id},candidate,0.4,{candidate_power},0.4,{speed}")
    return rows


def run_csv(text):
    return coil_bench.analyze_measurements(coil_bench.parse_measurements(io.StringIO(text)))


class CoilBenchTests(unittest.TestCase):
    def test_mechanical_output_power_and_efficiency(self):
        records = coil_bench.parse_measurements(
            io.StringIO(csv_text(complete_rows()))
        )
        baseline = records[0]
        self.assertAlmostEqual(baseline.output_power_w, 0.4 * 2 * math.pi * 3000 / 60)
        self.assertAlmostEqual(
            baseline.efficiency_pct,
            100 * (0.4 * 2 * math.pi * 3000 / 60) / 200,
        )

    def test_paired_change_and_uncertainty_summary(self):
        summary = run_csv(csv_text(complete_rows()))
        self.assertEqual(summary.pair_count, 2)
        self.assertGreater(summary.mean_change_percentage_points, 0)
        self.assertLess(summary.ci95_low_percentage_points, summary.mean_change_percentage_points)
        self.assertGreater(summary.ci95_high_percentage_points, summary.mean_change_percentage_points)
        self.assertAlmostEqual(
            summary.mean_change_percentage_points,
            sum(p.change_percentage_points for p in summary.pairs) / 2,
        )
        self.assertIn("Student's t", summary.ci_method)

    def test_pre_specified_threshold_assessment(self):
        summary = run_csv(csv_text(complete_rows()))
        above = dataclasses.replace(
            summary, ci95_low_percentage_points=3.0, ci95_high_percentage_points=5.0
        )
        below = dataclasses.replace(
            summary, ci95_low_percentage_points=0.0, ci95_high_percentage_points=1.0
        )
        overlapping = dataclasses.replace(
            summary, ci95_low_percentage_points=1.0, ci95_high_percentage_points=3.0
        )
        self.assertIn("entirely above", coil_bench.assess_threshold(above, 2.0))
        self.assertIn("entirely below", coil_bench.assess_threshold(below, 2.0))
        self.assertIn("overlaps", coil_bench.assess_threshold(overlapping, 2.0))

    def test_invalid_threshold_is_rejected(self):
        summary = run_csv(csv_text(complete_rows()))
        for threshold in (-0.1, float("nan"), float("inf")):
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(coil_bench.DataError, "finite and non-negative"):
                    coil_bench.assess_threshold(summary, threshold)

    def test_unpaired_measurement_is_rejected(self):
        text = csv_text(["A,baseline,0.4,200,0.4,3000"])
        with self.assertRaisesRegex(coil_bench.DataError, "unpaired"):
            run_csv(text)

    def test_duplicate_configuration_is_rejected(self):
        rows = [
            "A,baseline,0.4,200,0.4,3000",
            "A,baseline,0.4,201,0.4,3000",
            "A,candidate,0.4,190,0.4,3000",
            "B,baseline,0.4,200,0.4,3000",
            "B,candidate,0.4,190,0.4,3000",
        ]
        with self.assertRaisesRegex(coil_bench.DataError, "duplicate baseline"):
            run_csv(csv_text(rows))

    def test_mismatched_load_is_rejected(self):
        rows = [
            "A,baseline,0.4,200,0.4,3000",
            "A,candidate,0.5,190,0.4,3000",
            "B,baseline,0.4,200,0.4,3000",
            "B,candidate,0.4,190,0.4,3000",
        ]
        with self.assertRaisesRegex(coil_bench.DataError, "load_nm values do not match"):
            run_csv(csv_text(rows))

    def test_nan_and_infinity_are_rejected(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                rows = complete_rows()
                rows[0] = rows[0].replace(",200,", f",{value},")
                with self.assertRaisesRegex(coil_bench.DataError, "finite"):
                    run_csv(csv_text(rows))

    def test_malformed_numeric_and_empty_field_are_rejected(self):
        rows = complete_rows()
        rows[0] = rows[0].replace(",200,", ",two-hundred,")
        with self.assertRaisesRegex(coil_bench.DataError, "must be a number"):
            run_csv(csv_text(rows))
        rows = complete_rows()
        rows[0] = rows[0].replace(",200,", ",,")
        with self.assertRaisesRegex(coil_bench.DataError, "input_power_w must not be blank"):
            run_csv(csv_text(rows))

    def test_wrong_column_schema_is_rejected(self):
        with self.assertRaisesRegex(coil_bench.DataError, "columns do not match"):
            run_csv(csv_text(complete_rows(), header="pair_id,configuration"))

    def test_impossible_efficiency_is_rejected(self):
        rows = complete_rows()
        rows[0] = "A,baseline,0.4,100,0.4,3000"
        with self.assertRaisesRegex(coil_bench.DataError, "exceeds 100%"):
            run_csv(csv_text(rows))

    def test_nonpositive_input_power_and_negative_readings_are_rejected(self):
        for row in (
            "A,baseline,0.4,0,0.4,3000",
            "A,baseline,-0.4,200,0.4,3000",
            "A,baseline,0.4,200,-0.4,3000",
            "A,baseline,0.4,200,0.4,-3000",
        ):
            with self.subTest(row=row):
                rows = [row, "A,candidate,0.4,190,0.4,3000", *complete_rows()[2:]]
                with self.assertRaises(coil_bench.DataError):
                    run_csv(csv_text(rows))

    def test_single_pair_is_rejected_for_uncertainty_estimation(self):
        rows = [
            "A,baseline,0.4,200,0.4,3000",
            "A,candidate,0.4,190,0.4,3000",
        ]
        with self.assertRaisesRegex(coil_bench.DataError, "at least two complete pairs"):
            run_csv(csv_text(rows))

    def test_duplicate_header_is_rejected(self):
        header = "pair_id,configuration,load_nm,input_power_w,shaft_torque_nm,speed_rpm,speed_rpm"
        with self.assertRaisesRegex(coil_bench.DataError, "duplicate column"):
            run_csv(csv_text(complete_rows(), header=header))


    # --- Added review tests -------------------------------------------------

    def _cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = coil_bench.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _write(self, directory, name, text, encoding="utf-8", newline=None):
        path = Path(directory) / name
        with path.open("w", encoding=encoding, newline=newline) as handle:
            handle.write(text)
        return path

    def test_t_critical_values_match_reference_quantiles(self):
        # Reference 0.975 quantiles of Student's t (independently computed).
        reference = {
            1: 12.706204736, 2: 4.302652730, 7: 2.364624252, 29: 2.045229642,
            30: 2.042272456, 31: 2.039513446, 40: 2.021075390, 60: 2.000297822,
            120: 1.979930405, 1000: 1.962339081,
        }
        for df, expected in reference.items():
            with self.subTest(df=df):
                self.assertAlmostEqual(coil_bench.t_critical_975(df), expected, delta=1e-6)
        values = [coil_bench.t_critical_975(df) for df in range(1, 500)]
        self.assertTrue(all(a > b for a, b in zip(values, values[1:], strict=False)))
        self.assertTrue(all(v > 1.959963984 for v in values))
        for bad in (0, -1, 2.0, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    coil_bench.t_critical_975(bad)

    def test_two_pair_interval_matches_hand_calculation(self):
        rows = [
            "A,baseline,0.4,200,0.4,3000", "A,candidate,0.4,190,0.4,3000",
            "B,baseline,0.4,200,0.4,2980", "B,candidate,0.4,195,0.4,2980",
        ]
        summary = run_csv(csv_text(rows))
        def eff(power, rpm):
            return 100 * 0.4 * 2 * math.pi * rpm / 60 / power

        d1 = eff(190, 3000) - eff(200, 3000)
        d2 = eff(195, 2980) - eff(200, 2980)
        mean = (d1 + d2) / 2
        sd = abs(d1 - d2) / math.sqrt(2)  # n - 1 = 1
        se = sd / math.sqrt(2)
        self.assertAlmostEqual(summary.mean_change_percentage_points, mean)
        self.assertAlmostEqual(summary.change_sd_percentage_points, sd)
        self.assertAlmostEqual(summary.change_se_percentage_points, se)
        self.assertAlmostEqual(summary.ci95_low_percentage_points, mean - 12.706205 * se)
        self.assertAlmostEqual(summary.ci95_high_percentage_points, mean + 12.706205 * se)
        self.assertEqual(summary.ci_method, "Student's t (95%, df=1)")

    def _many_pairs(self, n):
        rows = []
        for index in range(n):
            rows.append(f"P{index:03d},baseline,0.4,200,0.4,3000")
            rows.append(f"P{index:03d},candidate,0.4,{190 + index % 7},0.4,3000")
        return run_csv(csv_text(rows))

    def test_large_samples_use_t_not_normal_critical_value(self):
        for n in (31, 32, 41, 121):
            with self.subTest(n=n):
                summary = self._many_pairs(n)
                margin = summary.ci95_high_percentage_points - summary.mean_change_percentage_points
                critical = margin / summary.change_se_percentage_points
                self.assertAlmostEqual(critical, coil_bench.t_critical_975(n - 1), places=9)
                self.assertGreater(critical, 1.96)
                self.assertEqual(summary.ci_method, f"Student's t (95%, df={n - 1})")

    def test_sample_sd_uses_n_minus_one(self):
        summary = self._many_pairs(9)
        changes = [pair.change_percentage_points for pair in summary.pairs]
        mean = sum(changes) / len(changes)
        manual = math.sqrt(sum((c - mean) ** 2 for c in changes) / (len(changes) - 1))
        self.assertAlmostEqual(summary.change_sd_percentage_points, manual)
        self.assertAlmostEqual(summary.change_sd_percentage_points, statistics.stdev(changes))

    def test_zero_spread_gives_no_interval_and_no_threshold_verdict(self):
        flat_rows = [
            "A,baseline,0.4,200,0.4,3000", "A,candidate,0.4,190,0.4,3000",
            "B,baseline,0.4,200,0.4,3000", "B,candidate,0.4,190,0.4,3000",
            "C,baseline,0.4,200,0.4,3000", "C,candidate,0.4,190,0.4,3000",
        ]
        summary = run_csv(csv_text(flat_rows))  # every pair changes by the same amount
        self.assertAlmostEqual(summary.change_sd_percentage_points, 0.0, places=9)
        self.assertIsNone(summary.ci95_low_percentage_points)
        self.assertIsNone(summary.ci95_high_percentage_points)
        self.assertIn("not estimable", summary.ci_method)
        self.assertIn("Not assessable", coil_bench.assess_threshold(summary, 0.0))
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "flat.csv", csv_text(flat_rows))
            code, out, _ = self._cli([str(path), "--threshold-pp", "1"])
            self.assertEqual(code, 0)
            self.assertIn("not estimable", out)
            code, out, _ = self._cli([str(path), "--threshold-pp", "1", "--json"])
            result = json.loads(out)
            self.assertEqual(result["ci95_percentage_points"], [None, None])
            self.assertIn("Not assessable", result["threshold_assessment"])

    def test_efficiency_boundaries(self):
        exact_100 = 0.4 * 2 * math.pi * 3000 / 60
        rows = [
            f"A,baseline,0.4,{exact_100!r},0.4,3000", "A,candidate,0.4,0.5,0,3000",
            "B,baseline,0.4,200,0.4,0", "B,candidate,0.4,190,0.4,3000",
        ]
        records = coil_bench.parse_measurements(io.StringIO(csv_text(rows)))
        self.assertAlmostEqual(records[0].efficiency_pct, 100.0)
        self.assertEqual(records[1].efficiency_pct, 0.0)
        self.assertEqual(records[2].efficiency_pct, 0.0)
        rows[1] = "A,candidate,0.4,125,0.4,3000"  # candidate row above 100%
        with self.assertRaisesRegex(coil_bench.DataError, "row 3: calculated efficiency exceeds 100%"):
            run_csv(csv_text(rows))
        with self.assertRaisesRegex(coil_bench.DataError, "not finite"):
            run_csv(csv_text(["A,baseline,0.4,200,1e300,1e300", *complete_rows()[1:]]))

    def test_non_plain_numbers_are_rejected(self):
        for value in ("1_000", "\u0662\u0660\u0660", "0x10", "Infinity", "+nan", "1e999", "2 00", "1.2.3"):
            with self.subTest(value=value):
                rows = complete_rows()
                rows[0] = rows[0].replace(",200,", f",{value},")
                with self.assertRaises(coil_bench.DataError):
                    run_csv(csv_text(rows))
        rows = complete_rows()
        rows[0] = "A,baseline, 4e-1 ,2.0E2,+.4,3000."
        self.assertEqual(run_csv(csv_text(rows)).pair_count, 2)

    def test_bom_header_whitespace_crlf_and_blank_lines(self):
        header = " pair_id , configuration,load_nm,input_power_w,shaft_torque_nm,speed_rpm "
        text = csv_text(complete_rows(), header=header).replace("\n", "\r\n")
        self.assertEqual(run_csv("\ufeff" + text).pair_count, 2)
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "bom.csv", csv_text(complete_rows()), encoding="utf-8-sig")
            self.assertEqual(coil_bench.analyze_file(path).pair_count, 2)
        rows = complete_rows()
        rows.insert(2, "")
        rows[3] = rows[3].replace(",200,", ",bad,")
        # Physical line 5 (header, two rows, a blank line, then the bad row).
        with self.assertRaisesRegex(coil_bench.DataError, "row 5: input_power_w must be a number"):
            run_csv(csv_text(rows))

    def test_encoding_nul_and_csv_errors_raise_data_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latin1.csv"
            path.write_bytes(csv_text(["A,baseline,0.4,200,0.4,3000", "A\xe9,candidate,0.4,190,0.4,3000"])
                             .encode("latin-1"))
            with self.assertRaisesRegex(coil_bench.DataError, "not valid UTF-8"):
                coil_bench.analyze_file(path)
            code, out, err = self._cli([str(path), "--threshold-pp", "1"])
            self.assertEqual((code, out), (2, ""))
            self.assertTrue(err.startswith("error: "))
        rows = complete_rows()
        rows[0] = rows[0].replace("A,", "A\x00,", 1)
        with self.assertRaises(coil_bench.DataError):
            run_csv(csv_text(rows))
        huge = "x" * (csv.field_size_limit() + 1)
        with self.assertRaisesRegex(coil_bench.DataError, "malformed CSV"):
            run_csv(csv_text([f"{huge},baseline,0.4,200,0.4,3000"]))

    def test_empty_and_header_only_files_are_rejected(self):
        with self.assertRaisesRegex(coil_bench.DataError, "empty"):
            run_csv("")
        with self.assertRaisesRegex(coil_bench.DataError, "no measurement rows"):
            run_csv(HEADER + "\n\n")

    def test_extra_or_missing_fields_are_rejected(self):
        rows = complete_rows()
        rows[0] += ",extra"
        with self.assertRaisesRegex(coil_bench.DataError, "too many"):
            run_csv(csv_text(rows))
        rows = complete_rows()
        rows[0] = "A,baseline,0.4,200,0.4"
        with self.assertRaisesRegex(coil_bench.DataError, "missing CSV field"):
            run_csv(csv_text(rows))

    def test_pairing_rules(self):
        rows = complete_rows()
        rows[1] = rows[1].replace("A,", " A ,", 1)  # whitespace around pair_id is ignored
        self.assertEqual(run_csv(csv_text(rows)).pair_count, 2)
        rows = complete_rows()
        rows[0] = rows[0].replace("baseline", "Baseline")
        with self.assertRaisesRegex(coil_bench.DataError, "configuration must be"):
            run_csv(csv_text(rows))
        rows = complete_rows()
        rows[1] = "A,candidate,0.4000000005,190,0.4,3000"  # within 1e-9 Nm
        self.assertEqual(run_csv(csv_text(rows)).pair_count, 2)
        rows[1] = "A,candidate,0.400000002,190,0.4,3000"
        with self.assertRaisesRegex(coil_bench.DataError, "do not match"):
            run_csv(csv_text(rows))
        with self.assertRaisesRegex(coil_bench.DataError, "missing baseline"):
            run_csv(csv_text([*complete_rows(), "C,candidate,0.4,190,0.4,3000"]))

    def test_threshold_equal_to_interval_bound_is_overlap(self):
        summary = self._many_pairs(5)
        self.assertIn("overlaps", coil_bench.assess_threshold(summary, summary.ci95_low_percentage_points))
        self.assertIn("overlaps", coil_bench.assess_threshold(summary, summary.ci95_high_percentage_points))

    def test_bundled_sample_results_are_pinned_and_labelled(self):
        code, out, _ = self._cli([str(SAMPLE), "--threshold-pp", "2.0", "--json"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertTrue(result["synthetic_sample"])
        self.assertEqual(result["notice"], "Synthetic illustrative data; not experimental evidence.")
        self.assertEqual(result["pair_count"], 8)
        self.assertAlmostEqual(result["mean_change_percentage_points"], 4.088751, places=5)
        self.assertAlmostEqual(result["change_sd_percentage_points"], 0.219250, places=5)
        self.assertEqual(result["ci_method"], "Student's t (95%, df=7)")
        low, high = result["ci95_percentage_points"]
        self.assertAlmostEqual(low, 3.905454, places=5)
        self.assertAlmostEqual(high, 4.272049, places=5)
        code, out, _ = self._cli([str(SAMPLE), "--threshold-pp", "2.0"])
        self.assertIn("NOTICE: Synthetic sample data", out)

    def test_copies_of_the_sample_are_still_labelled_synthetic(self):
        with tempfile.TemporaryDirectory() as directory:
            plain_copy = Path(directory) / "copy.csv"
            shutil.copy(SAMPLE, plain_copy)
            lines = SAMPLE.read_text(encoding="utf-8").splitlines()
            reordered = self._write(directory, "reordered.csv",
                                    "\ufeff" + "\r\n".join([lines[0], *reversed(lines[1:])]) + "\r\n",
                                    newline="")
            edited = self._write(directory, "edited.csv",
                                 SAMPLE.read_text(encoding="utf-8").replace(",174,", ",175,"))
            for path, expected in ((plain_copy, True), (reordered, True), (edited, False)):
                with self.subTest(path=path.name):
                    code, out, _ = self._cli([str(path), "--threshold-pp", "2", "--json"])
                    self.assertEqual(code, 0)
                    result = json.loads(out)
                    self.assertIs(result["synthetic_sample"], expected)
                    if not expected:
                        self.assertIsNone(result["notice"])

    def test_cli_argument_and_file_errors_exit_2(self):
        for threshold in ("-1", "nan", "inf"):
            with self.subTest(threshold=threshold):
                code, out, err = self._cli([str(SAMPLE), "--threshold-pp", threshold])
                self.assertEqual((code, out), (2, ""))
                self.assertIn("finite and non-negative", err)
        code, out, err = self._cli([str(ROOT / "missing.csv"), "--threshold-pp", "1"])
        self.assertEqual((code, out), (2, ""))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            coil_bench.main([str(SAMPLE)])  # --threshold-pp is required
        self.assertEqual(raised.exception.code, 2)

    def test_module_imports_only_offline_standard_library_modules(self):
        tree = ast.parse((ROOT / "coil_bench.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        allowed = {"__future__", "argparse", "csv", "dataclasses", "json", "math", "pathlib",
                   "re", "statistics", "sys", "typing"}
        self.assertLessEqual(imported, allowed)

    def test_load_nm_only_matches_pairs_and_does_not_affect_efficiency(self):
        rows_a = complete_rows()
        rows_b = [row.replace(",0.4,200,", ",5.0,200,").replace(",0.4,190,", ",5.0,190,")
                  for row in rows_a]
        self.assertNotEqual(rows_a, rows_b)
        summary_a, summary_b = run_csv(csv_text(rows_a)), run_csv(csv_text(rows_b))
        self.assertEqual(
            [p.change_percentage_points for p in summary_a.pairs],
            [p.change_percentage_points for p in summary_b.pairs],
        )
        self.assertEqual([p.load_nm for p in summary_b.pairs], [5.0, 5.0])

    @unittest.skipIf(scipy_stats is None, "SciPy not installed (optional cross-check)")
    def test_interval_matches_scipy_paired_t_test(self):
        datasets = {"sample": coil_bench.read_measurements(SAMPLE)}
        rows = []
        for index in range(45):
            rows.append(f"P{index:02d},baseline,0.4,200,0.4,3000")
            rows.append(f"P{index:02d},candidate,0.4,{188 + (index * 7) % 13},0.4,3000")
        datasets["45 pairs"] = coil_bench.parse_measurements(io.StringIO(csv_text(rows)))
        for name, measurements in datasets.items():
            with self.subTest(name):
                summary = coil_bench.analyze_measurements(measurements)
                candidate = [p.candidate_efficiency_pct for p in summary.pairs]
                baseline = [p.baseline_efficiency_pct for p in summary.pairs]
                reference = scipy_stats.ttest_rel(candidate, baseline)
                interval = reference.confidence_interval(0.95)
                self.assertAlmostEqual(summary.ci95_low_percentage_points, interval.low, delta=1e-6)
                self.assertAlmostEqual(summary.ci95_high_percentage_points, interval.high, delta=1e-6)

    def test_version_is_single_sourced_and_matches_citation(self):
        citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
        match = re.search(r"^version: (\S+)$", citation, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(coil_bench.__version__, match.group(1))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            coil_bench.main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn(coil_bench.__version__, out.getvalue())


if __name__ == "__main__":
    unittest.main()
