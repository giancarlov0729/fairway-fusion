"""Verify Person 5 inputs and calculation handoff locally or on the Pi."""

import argparse
import json
from urllib.request import urlopen

from fairway_fusion.simulation import (
    SCENARIOS,
    INVALID_CASES,
    generate_shot,
    invalid_shot,
    analyze_samples,
)


def verify():
    results = []
    for name, (speed, angle, direction) in SCENARIOS.items():
        result = analyze_samples(generate_shot(name))
        if any(
            abs(result[key] - expected) > 1e-8
            for key, expected in (
                ("speed_m_s", speed),
                ("launch_angle_deg", angle),
                ("launch_direction_deg", direction),
            )
        ):
            raise AssertionError(f"Calculation handoff failed: {name}")
        results.append({"scenario": name, "status": "passed", "result": result})
    for case in INVALID_CASES:
        try:
            analyze_samples(invalid_shot(case))
        except ValueError:
            results.append({"scenario": case, "status": "correctly rejected"})
        else:
            raise AssertionError(f"Invalid sample was accepted: {case}")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server-url",
        help="Optional deployed dashboard URL for an HTTP availability check",
    )
    args = parser.parse_args()
    report = {"simulation_and_calculation": verify()}
    if args.server_url:
        with urlopen(args.server_url, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("Dashboard unavailable")
            report["dashboard_http_status"] = response.status
        report["scope"] = (
            "HTTP availability only; session persistence must be checked with the team server"
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
