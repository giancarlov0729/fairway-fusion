"""Connect simulated observations to the team's shot calculations."""

from uuid import uuid4                          # Used to generate unique shot preview ID

from fairway_fusion.simulation import (
    SCENARIOS,                                      # Names of available simulated trajectories
    generate_shot,                                  # Produces positions and capture timestamps
    calculation_input,                              # Converts simulator mm/ms into meters/seconds
)

from fairway_fusion.analysis.velocity import estimate_velocity      # Function to calculate velocity from positions and timestamps
from fairway_fusion.analysis.carry import carry_metrics             # Function to estimate carry (model output, not a measurement)
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
        "source": "simulation",                             # Identifies the observations as simulated
        "scenario": scenario,                               # Identify the chosen sample trajectory
        "saved": False,                                     # Explicitly indicate that no database write occurred
        "velocity_m_s": velocity.tolist(),                  # Convert the NumPy array into a list
        "samples": {
            "positions_m": positions_m.tolist(),
            "timestamps_s": times_s.tolist(),
        },
        **metrics,                                          # Insert each calculated metric into this dictionary
        "carry": carry_metrics(metrics),                    # Estimated carry kept in its own block, separate from measured values
    }

def save_simulated_shot(store, session_id, scenario="straight"):    # Function to generate a simulated shot, calculate metrics, and save results and input samples to database
    result = preview_shot(scenario)                                     # Generate simulated shot + calculate metrics + store results in a dictionary
    return store.save_shot(                                             # Call database object's save_shot() method to save the results and input samples to the database
        session_id=session_id,                                              # Associate the shot with the current session
        speed_m_s=result["speed_m_s"],                                      # Store the calculated speed in m/s
        launch_angle_deg=result["launch_angle_deg"],                        # Store the calculated launch angle in degrees
        launch_direction_deg=result["launch_direction_deg"],                # Store the calculated launch direction in degrees
        source="simulation",                                                # Store the source of the shot as "simulation"
        samples=result["samples"],                                          # Store the input samples (positions and timestamps) in the database
    )