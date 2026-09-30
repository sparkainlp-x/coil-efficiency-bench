#!/usr/bin/env python3
# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""Offline paired analysis of baseline and candidate motor measurements."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

REQUIRED_COLUMNS = (
    "pair_id",
    "configuration",
    "load_nm",
    "input_power_w",
    "shaft_torque_nm",
    "speed_rpm",
)
CONFIGURATIONS = {"baseline", "candidate"}
BUNDLED_SYNTHETIC_SAMPLE = (
    Path(__file__).resolve().parent / "data" / "synthetic_measurements.csv"
)

# Two-sided 95% Student-t critical values (0.975 quantiles) for df 1..30.
_T_975 = (
    12.706205, 4.302653, 3.182446, 2.776445, 2.570582, 2.446912,
    2.364624, 2.306004, 2.262157, 2.228139, 2.200985, 2.178813,
    2.160369, 2.144787, 2.131450, 2.119905, 2.109816, 2.100922,
    2.093024, 2.085963, 2.079614, 2.073873, 2.068658, 2.063899,
    2.059539, 2.055529, 2.051831, 2.048407, 2.045230, 2.042272,
)
# 0.975 quantile of the standard normal distribution.
_Z_975 = 1.959963984540054
# Plain decimal numbers only (ASCII digits, optional exponent). Python's float()
# would also accept forms such as "1_000", non-ASCII digits, "nan", and "inf".
_NUMBER_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
# Paired changes whose range is at most this many percentage points are treated
# as having zero spread, for which no meaningful interval can be computed.
_ZERO_SPREAD_PP = 1e-9


def t_critical_975(degrees_of_freedom: int) -> float:
    """Two-sided 95% Student-t critical value for ``degrees_of_freedom`` >= 1.

    Uses a table for df <= 30 and, above that, the Cornish-Fisher expansion of
    the t quantile around the normal quantile (absolute error below 1e-7 for
    df > 30). It never falls back to the normal value 1.96.
    """
    if type(degrees_of_freedom) is not int or degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be a positive integer")
    if degrees_of_freedom <= len(_T_975):
        return _T_975[degrees_of_freedom - 1]
    z = _Z_975
    v = float(degrees_of_freedom)
    return (
        z
        + (z**3 + z) / (4 * v)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * v**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * v**3)
        + (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / (92160 * v**4)
    )


class DataError(ValueError):
    """Raised when the input is malformed or cannot support a paired analysis."""


@dataclass(frozen=True)
class Measurement:
    pair_id: str
    configuration: str
    load_nm: float
    input_power_w: float
    shaft_torque_nm: float
    speed_rpm: float
    output_power_w: float
    efficiency_pct: float


@dataclass(frozen=True)
class PairResult:
    pair_id: str
    load_nm: float
    baseline_efficiency_pct: float
    candidate_efficiency_pct: float
    change_percentage_points: float


@dataclass(frozen=True)
class Summary:
    pair_count: int
    baseline_mean_efficiency_pct: float
    candidate_mean_efficiency_pct: float
    mean_change_percentage_points: float
    change_sd_percentage_points: float
    change_se_percentage_points: float
    # None when the paired changes have zero spread (no meaningful interval).
    ci95_low_percentage_points: float | None
    ci95_high_percentage_points: float | None
    ci_method: str
    pairs: tuple[PairResult, ...]


def _finite_number(raw: str, field: str, row_number: int) -> float:
    text = raw.strip()
    if text.lower().lstrip("+-") in {"nan", "inf", "infinity"}:
        raise DataError(f"row {row_number}: {field} must be finite; got {raw!r}")
    if not _NUMBER_RE.fullmatch(text):
        raise DataError(f"row {row_number}: {field} must be a number; got {raw!r}")
    value = float(text)
    if not math.isfinite(value):  # e.g. 1e999 overflows to infinity
        raise DataError(f"row {row_number}: {field} must be finite; got {raw!r}")
    return value


def parse_measurements(stream: TextIO) -> list[Measurement]:
    """Read CSV rows, calculate efficiencies, and reject invalid readings."""
    try:
        return _parse_measurements(stream)
    except csv.Error as exc:
        raise DataError(f"malformed CSV: {exc}") from None


def _parse_measurements(stream: TextIO) -> list[Measurement]:
    reader = csv.DictReader(stream)
    raw_headers = reader.fieldnames
    if raw_headers is None:
        raise DataError("CSV is empty; expected a header row")
    # Tolerate surrounding whitespace and a byte-order mark on the header names.
    headers = [name.strip() for name in raw_headers]
    if headers:
        headers[0] = headers[0].lstrip("\ufeff").strip()
    reader.fieldnames = headers
    if len(headers) != len(set(headers)):
        raise DataError("CSV header contains duplicate column names")
    if set(headers) != set(REQUIRED_COLUMNS) or len(headers) != len(REQUIRED_COLUMNS):
        missing = sorted(set(REQUIRED_COLUMNS) - set(headers))
        unexpected = sorted(set(headers) - set(REQUIRED_COLUMNS))
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise DataError("CSV columns do not match the required schema (" + "; ".join(details) + ")")

    measurements: list[Measurement] = []
    for row in reader:
        row_number = reader.line_num  # physical line; correct even after blank lines
        if None in row:
            raise DataError(f"row {row_number}: too many CSV fields")
        if all(value is None or not value.strip() for value in row.values()):
            continue
        if any(value is None for value in row.values()):
            raise DataError(f"row {row_number}: missing CSV field value")
        if any("\x00" in value for value in row.values()):
            # Python 3.10's csv module rejects NUL bytes; 3.11+ accepts them.
            raise DataError(f"row {row_number}: NUL character in CSV field")
        for field, value in row.items():
            if not value.strip():
                raise DataError(f"row {row_number}: {field} must not be blank")

        pair_id = row["pair_id"].strip()
        if not pair_id:
            raise DataError(f"row {row_number}: pair_id must not be blank")
        configuration = row["configuration"].strip()
        if configuration not in CONFIGURATIONS:
            raise DataError(
                f"row {row_number}: configuration must be 'baseline' or 'candidate'; got {configuration!r}"
            )

        load_nm = _finite_number(row["load_nm"], "load_nm", row_number)
        input_power_w = _finite_number(row["input_power_w"], "input_power_w", row_number)
        shaft_torque_nm = _finite_number(row["shaft_torque_nm"], "shaft_torque_nm", row_number)
        speed_rpm = _finite_number(row["speed_rpm"], "speed_rpm", row_number)
        if load_nm < 0:
            raise DataError(f"row {row_number}: load_nm must be non-negative")
        if input_power_w <= 0:
            raise DataError(f"row {row_number}: input_power_w must be greater than zero")
        if shaft_torque_nm < 0:
            raise DataError(f"row {row_number}: shaft_torque_nm must be non-negative")
        if speed_rpm < 0:
            raise DataError(f"row {row_number}: speed_rpm must be non-negative")

        output_power_w = shaft_torque_nm * (2.0 * math.pi * speed_rpm / 60.0)
        if not math.isfinite(output_power_w):
            raise DataError(f"row {row_number}: calculated mechanical output power is not finite")
        efficiency = output_power_w / input_power_w
        if not math.isfinite(efficiency):
            raise DataError(f"row {row_number}: calculated efficiency is not finite")
        if efficiency > 1.0 + 1e-9:
            raise DataError(
                f"row {row_number}: calculated efficiency exceeds 100%; check units and readings"
            )
        measurements.append(
            Measurement(
                pair_id=pair_id,
                configuration=configuration,
                load_nm=load_nm,
                input_power_w=input_power_w,
                shaft_torque_nm=shaft_torque_nm,
                speed_rpm=speed_rpm,
                output_power_w=output_power_w,
                efficiency_pct=100.0 * efficiency,
            )
        )

    if not measurements:
        raise DataError("CSV has a header but no measurement rows")
    return measurements


def analyze_measurements(measurements: list[Measurement]) -> Summary:
    """Validate complete load-matched pairs and summarize paired changes."""
    grouped: dict[str, dict[str, Measurement]] = {}
    for measurement in measurements:
        group = grouped.setdefault(measurement.pair_id, {})
        if measurement.configuration in group:
            raise DataError(
                f"pair {measurement.pair_id!r}: duplicate {measurement.configuration} reading"
            )
        group[measurement.configuration] = measurement

    pair_results: list[PairResult] = []
    for pair_id in sorted(grouped):
        group = grouped[pair_id]
        if set(group) != CONFIGURATIONS:
            missing = sorted(CONFIGURATIONS - set(group))
            raise DataError(f"pair {pair_id!r}: unpaired; missing {', '.join(missing)} reading")
        baseline = group["baseline"]
        candidate = group["candidate"]
        if not math.isclose(baseline.load_nm, candidate.load_nm, rel_tol=0.0, abs_tol=1e-9):
            raise DataError(
                f"pair {pair_id!r}: baseline and candidate load_nm values do not match"
            )
        pair_results.append(
            PairResult(
                pair_id=pair_id,
                load_nm=(baseline.load_nm + candidate.load_nm) / 2.0,
                baseline_efficiency_pct=baseline.efficiency_pct,
                candidate_efficiency_pct=candidate.efficiency_pct,
                change_percentage_points=(
                    candidate.efficiency_pct - baseline.efficiency_pct
                ),
            )
        )

    n = len(pair_results)
    if n < 2:
        raise DataError("at least two complete pairs are required to estimate paired uncertainty")

    changes = [pair.change_percentage_points for pair in pair_results]
    mean_change = statistics.fmean(changes)
    change_sd = statistics.stdev(changes)
    change_se = change_sd / math.sqrt(n)
    degrees_of_freedom = n - 1
    ci_low: float | None
    ci_high: float | None
    if max(changes) - min(changes) <= _ZERO_SPREAD_PP:
        # A zero-width interval would falsely suggest perfect certainty.
        ci_low = ci_high = None
        ci_method = "not estimable: paired changes have zero spread"
    else:
        margin = t_critical_975(degrees_of_freedom) * change_se
        ci_low = mean_change - margin
        ci_high = mean_change + margin
        ci_method = f"Student's t (95%, df={degrees_of_freedom})"

    return Summary(
        pair_count=n,
        baseline_mean_efficiency_pct=statistics.fmean(
            pair.baseline_efficiency_pct for pair in pair_results
        ),
        candidate_mean_efficiency_pct=statistics.fmean(
            pair.candidate_efficiency_pct for pair in pair_results
        ),
        mean_change_percentage_points=mean_change,
        change_sd_percentage_points=change_sd,
        change_se_percentage_points=change_se,
        ci95_low_percentage_points=ci_low,
        ci95_high_percentage_points=ci_high,
        ci_method=ci_method,
        pairs=tuple(pair_results),
    )


def read_measurements(path: Path) -> list[Measurement]:
    """Read and validate a UTF-8 (optionally BOM-prefixed) measurement CSV file."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            return parse_measurements(csv_file)
    except UnicodeDecodeError as exc:
        raise DataError(f"{path}: file is not valid UTF-8 text ({exc.reason})") from None


def analyze_file(path: Path) -> Summary:
    return analyze_measurements(read_measurements(path))


def _measurement_key(measurements: list[Measurement]) -> list[tuple[object, ...]]:
    return sorted(
        (m.pair_id, m.configuration, m.load_nm, m.input_power_w, m.shaft_torque_nm, m.speed_rpm)
        for m in measurements
    )


def _is_bundled_synthetic_sample(
    path: Path, measurements: list[Measurement] | None = None
) -> bool:
    """True for the bundled sample file or any unmodified copy of its readings.

    Copies are recognised by identical parsed readings (row order, line endings,
    BOM, and number formatting do not matter). An edited copy is not recognised.
    """
    try:
        if path.resolve() == BUNDLED_SYNTHETIC_SAMPLE.resolve():
            return True
    except OSError:
        pass
    if measurements is None:
        return False
    try:
        bundled = read_measurements(BUNDLED_SYNTHETIC_SAMPLE)
    except (OSError, DataError):
        return False
    return _measurement_key(measurements) == _measurement_key(bundled)


def assess_threshold(summary: Summary, minimum_improvement_pp: float) -> str:
    """Describe whether the 95% paired interval clears a pre-set threshold."""
    if not math.isfinite(minimum_improvement_pp) or minimum_improvement_pp < 0:
        raise DataError("minimum improvement threshold must be finite and non-negative")
    if summary.ci95_low_percentage_points is None or summary.ci95_high_percentage_points is None:
        return "Not assessable: paired changes have zero spread, so no interval was computed."
    if summary.ci95_low_percentage_points > minimum_improvement_pp:
        return "95% CI is entirely above the pre-specified threshold."
    if summary.ci95_high_percentage_points < minimum_improvement_pp:
        return "95% CI is entirely below the pre-specified threshold."
    return "95% CI overlaps the pre-specified threshold."


def _summary_dict(
    summary: Summary,
    input_path: Path,
    synthetic_sample: bool,
    minimum_improvement_pp: float,
) -> dict[str, object]:
    return {
        "input_file": input_path.name,
        "synthetic_sample": synthetic_sample,
        "notice": (
            "Synthetic illustrative data; not experimental evidence."
            if synthetic_sample
            else None
        ),
        "pair_count": summary.pair_count,
        "baseline_mean_efficiency_pct": summary.baseline_mean_efficiency_pct,
        "candidate_mean_efficiency_pct": summary.candidate_mean_efficiency_pct,
        "mean_change_percentage_points": summary.mean_change_percentage_points,
        "change_sd_percentage_points": summary.change_sd_percentage_points,
        "change_se_percentage_points": summary.change_se_percentage_points,
        "ci95_percentage_points": [
            summary.ci95_low_percentage_points,
            summary.ci95_high_percentage_points,
        ],
        "ci_method": summary.ci_method,
        "minimum_improvement_threshold_percentage_points": minimum_improvement_pp,
        "threshold_assessment": assess_threshold(summary, minimum_improvement_pp),
        "pairs": [
            {
                "pair_id": pair.pair_id,
                "load_nm": pair.load_nm,
                "baseline_efficiency_pct": pair.baseline_efficiency_pct,
                "candidate_efficiency_pct": pair.candidate_efficiency_pct,
                "change_percentage_points": pair.change_percentage_points,
            }
            for pair in summary.pairs
        ],
    }


def _print_text(
    summary: Summary,
    input_path: Path,
    synthetic_sample: bool,
    minimum_improvement_pp: float,
) -> None:
    print("Paired motor-efficiency comparison")
    print(f"Input: {input_path}")
    if synthetic_sample:
        print("NOTICE: Synthetic sample data; illustrative only, not experimental evidence.")
    print(f"Complete load-matched pairs: {summary.pair_count}")
    print(f"Baseline mean efficiency:    {summary.baseline_mean_efficiency_pct:.3f}%")
    print(f"Candidate mean efficiency:   {summary.candidate_mean_efficiency_pct:.3f}%")
    print(
        "Mean paired change:          "
        f"{summary.mean_change_percentage_points:+.3f} percentage points"
    )
    print(
        "Paired change SD / SE:       "
        f"{summary.change_sd_percentage_points:.3f} / "
        f"{summary.change_se_percentage_points:.3f} percentage points"
    )
    if summary.ci95_low_percentage_points is None or summary.ci95_high_percentage_points is None:
        print("95% confidence interval:     not estimable (zero spread in paired changes)")
    else:
        print(
            "95% confidence interval:     "
            f"[{summary.ci95_low_percentage_points:+.3f}, "
            f"{summary.ci95_high_percentage_points:+.3f}] percentage points"
        )
    print(f"Interval method:              {summary.ci_method}")
    print(f"Pre-specified improvement threshold: {minimum_improvement_pp:.3f} percentage points")
    print(f"Threshold assessment:         {assess_threshold(summary, minimum_improvement_pp)}")
    print("Interpretation is limited to the tested hardware and conditions.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare paired baseline/candidate motor efficiency measurements offline."
    )
    parser.add_argument("csv_path", type=Path, help="CSV file using the documented measurement schema")
    parser.add_argument(
        "--threshold-pp",
        type=float,
        required=True,
        help="pre-specified minimum meaningful efficiency gain in percentage points (>= 0)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="write a JSON result instead of text"
    )
    args = parser.parse_args(argv)

    if not math.isfinite(args.threshold_pp) or args.threshold_pp < 0:
        print("error: --threshold-pp must be finite and non-negative", file=sys.stderr)
        return 2

    try:
        measurements = read_measurements(args.csv_path)
        summary = analyze_measurements(measurements)
    except (OSError, DataError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    synthetic_sample = _is_bundled_synthetic_sample(args.csv_path, measurements)
    if args.as_json:
        print(
            json.dumps(
                _summary_dict(summary, args.csv_path, synthetic_sample, args.threshold_pp),
                indent=2,
            )
        )
    else:
        _print_text(summary, args.csv_path, synthetic_sample, args.threshold_pp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
