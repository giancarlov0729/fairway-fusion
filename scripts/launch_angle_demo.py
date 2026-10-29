"""Run after installing the project: python scripts/launch_angle_demo.py."""

import math

from fairway_fusion.analysis.launch_angle import estimate_launch_angle


def main():
    # Synthetic 15-degree launch: these are NOT measurements from cameras.
    times = [0, 0.0005, 0.001, 0.0015, 0.002]
    vx = 50.0  # meters per second downrange
    vz = vx * math.tan(math.radians(15))
    positions = [(vx * t, 0, 0.03 + vz * t) for t in times]
    angle = estimate_launch_angle(positions, times)
    print("SIMULATED DATA - not a measured golf shot")
    print(f"Expected: 15.00 degrees | Estimated: {angle:.2f} degrees")


if __name__ == "__main__":
    main()
