"""Connect simulated observations to the team's shot calculations."""

from uuid import uuid4                          # Used to generate unique shot preview ID

from fairway_fusion.simulation import (
    SCENARIOS,                                      # Names of available simulated trajectories
    generate_shot,                                  # Produces positions and capture timestamps
    calculation_input,                              # Converts simulator mm/ms into meters/seconds
)

from fairway_fusion.analysis.velocity import estimate_velocity      # Function to calculate velocity from positions and timestamps
from fairway_fusion.analysis.launch_angle import launch_metrics     # Function to calcualte ball speed, launch angle, and azimuth from velocity


def preview_shot(scenario="straight"):                              # Function to generate a simulated shot preview based on a given scenario
    """Calculate one simulated shot without storing it."""

    if not isinstance(scenario, str) or scenario not in SCENARIOS:              # Validate selection
        raise ValueError("Choose straight, high-launch, or right-launch.")

    packet = generate_shot(scenario)                                            # Generates simulated positions and timestamps for the chosen scenario
    positions_m, times_s = calculation_input(packet)                            # Validates and converts output from sim to meters and seconds
    velocity = estimate_velocity(positions_m, times_s)                          # Estimate vx, vy, and vz
    metrics = launch_metrics(velocity)                                          # Calculate ball speed, launch angle, and azimuth from the estimated velocity

    if metrics["launch_angle_deg"] is None:                                     # A stationary ball = no launch angle
        raise ValueError("The observations do not describe a valid launch.")

    return {                                                
        "preview_id": str(uuid4()),                         # Newly generated ID converted to string
        "source": "simulation",                             # Labels data as simulated rather than persisted
        "scenario": scenario,                               # Identify the chosen sample trajectory
        "saved": False,                                     # Explicitly indicate that no database write occurred
        "velocity_m_s": velocity.tolist(),                  # Convert the NumPy array into a list
        **metrics,                                          # Insert each calculated metric into this dictionary
    }