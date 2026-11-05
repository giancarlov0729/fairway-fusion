"""Exercise the database on its own, without the UI or the server.

Usage, from the repository root:

    python scripts/storage_demo.py            # save 3 simulated shots, print history
    python scripts/storage_demo.py --history  # only print history (check persistence)
    python scripts/storage_demo.py --db demo.db   # use a separate file

What it does:
    1. Opens the database (the same src/instance/fairway.db the server uses, unless
       --db or the FAIRWAY_DB environment variable says otherwise).
    2. Starts a session, generates three simulated shots, calculates launch
       angle and speed for each, and saves them.
    3. Saves one rejected shot, to show rejections stay visible.
    4. Ends the session and prints recent history.

Running it a second time with --history and seeing the same shots proves the
data persisted on disk. Every value here is SIMULATED, not a measurement.
"""

import argparse  # reads command-line options like --history and --db
import math  # cos and sin, to turn speed + angle into x and z velocity
import random  # random speeds, angles and small position noise

from fairway_fusion.analysis.velocity import estimate_velocity
from fairway_fusion.analysis.launch_angle import calculate_ball_speed

# The team's existing launch angle function from the analysis module.
from fairway_fusion.analysis.launch_angle import estimate_launch_angle
from fairway_fusion.storage import ShotStore


def simulated_samples(speed_m_s, angle_deg, rng):
    """Build fake ball positions at 1, 2, 3 and 4 ms after impact.

    The ball moves in a straight line at the given speed and launch angle,
    matching the team's example (x around 50 mm at 1 ms for a 50 m/s shot).
    Coordinates follow the launch angle module: x downrange, y sideways,
    z up, all in metres.

    Returns a dict in the same shape the database stores as raw samples.
    """
    times = [0.001, 0.002, 0.003, 0.004]  # seconds after impact

    # Split the speed into its forward (x) and upward (z) parts.
    vx = speed_m_s * math.cos(math.radians(angle_deg))
    vz = speed_m_s * math.sin(math.radians(angle_deg))

    # Position = velocity x time. The ball starts 0.03 m off the ground
    # (roughly sitting on a tee). rng.gauss adds about 0.2 mm of random
    # noise to x and y, so the data is not perfectly clean, like a real
    # camera measurement would not be.
    positions = [
        [vx * t + rng.gauss(0, 0.0002), rng.gauss(0, 0.0002), 0.03 + vz * t]
        for t in times
    ]
    return {"timestamps_s": times, "positions_m": positions}


def fitted_speed(samples):
    velocity = estimate_velocity(samples["positions_m"], samples["timestamps_s"])
    return calculate_ball_speed(velocity)


def print_history(store):
    """Print the 5 most recent sessions and up to 10 shots in each."""
    for session in store.list_sessions(limit=5):
        state = "active" if session["active"] else "ended"
        print(f"Session {session['id']} ({state}, {session['shot_count']} shots)")
        for shot in store.list_shots(session["id"], limit=10):
            if shot["status"] == "accepted":
                # :6.1f means "6 characters wide, 1 decimal place", which
                # lines the numbers up in columns.
                print(
                    f"  #{shot['id']}  {shot['speed_mph']:6.1f} mph  "
                    f"{shot['launch_angle_deg']:5.1f} deg  {shot['created_at']}"
                )
            else:
                print(f"  #{shot['id']}  REJECTED: {shot['reason']}")


def main():
    # Command-line options. __doc__ is the description at the top of this
    # file, so "--help" prints it.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Database path (default src/instance/fairway.db)")
    parser.add_argument("--history", action="store_true", help="Only print history")
    args = parser.parse_args()

    # args.db is None unless --db was given, so ShotStore falls back to
    # FAIRWAY_DB, then the default path.
    store = ShotStore(args.db)
    print(f"Database: {store.path}")

    if not args.history:
        if store.active_session() is not None:
            parser.error("A session is already active. End it first or use --db with a separate file.")
        store.start_session()

        rng = random.Random()
        for _ in range(3):
            # Pick a plausible shot: 40 to 70 m/s, 8 to 20 degrees.
            samples = simulated_samples(rng.uniform(40, 70), rng.uniform(8, 20), rng)
            # Run the calculations, then save the results with the raw input.
            store.save_shot(
                fitted_speed(samples),
                estimate_launch_angle(samples["positions_m"], samples["timestamps_s"]),
                samples=samples,
            )

        # One rejected shot, to show it is stored and listed with its reason.
        store.save_shot(status="rejected", reason="Simulated invalid sample")
        store.end_session()

    print_history(store)


# Only run main() when this file is executed directly, not when imported.
if __name__ == "__main__":
    main()