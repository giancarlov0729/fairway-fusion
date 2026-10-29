# Fairway Fusion

Fairway Fusion is a dual-camera computer vision golf launch monitor developed as a Computer Engineering capstone project at the University of Florida.

The system aims to measure golf ball launch characteristics using high-speed imaging and computer vision techniques.

## Project Objectives

The primary measurements are:

- Ball speed (mph)
- Launch angle (degrees)
- Launch direction (degrees)
- Spin rate (RPM)
- Spin axis (degrees)

The system uses two cameras to observe a golf ball immediately after impact. High-contrast markers placed on the ball will assist in estimating its rotation.

## System Architecture

1. Camera Capture
2. Frame Synchronization
3. Camera Calibration
4. Ball Detection
5. Ball Tracking
6. 3D Trajectory Reconstruction
7. Marker Detection and Tracking
8. Spin Estimation
9. Shot Validation
10. Shot Metrics Output

## Technology Stack

- Python 3.10+
- OpenCV
- NumPy
- SciPy
- Matplotlib
- PyYAML
- pytest

## Installation

Clone the repository:

    git clone https://github.com/giancarlov0729/fairway-fusion.git
    cd fairway-fusion

Create a virtual environment:

    python3 -m venv .venv
    source .venv/bin/activate

Install dependencies:

    python -m pip install -r requirements.txt
    python -m pip install -e .

## Repository Structure

- config/ - Camera and processing configuration
- src/fairway_fusion/cameras/ - Camera capture and synchronization
- src/fairway_fusion/calibration/ - Camera calibration
- src/fairway_fusion/detection/ - Ball and marker detection
- src/fairway_fusion/tracking/ - Ball and marker tracking
- src/fairway_fusion/analysis/ - Launch and spin calculations
- scripts/ - Development and execution scripts
- tests/ - Automated tests
- data/ - Calibration data and recordings
- docs/ - Technical documentation

## Development Status

Current phase: Pre-alpha architectural skeleton.

A basic vertical launch-angle calculation is implemented using calibrated 3D
positions and timestamps. A synthetic demo and automated tests run without cameras:

    python scripts/launch_angle_demo.py
    python -m pytest

See [launch-angle setup, assumptions, and known limitations](docs/launch_angle.md).
Camera capture, calibration, and ball tracking remain unimplemented; demo output
is simulated and does not establish measurement accuracy.

The computer vision algorithms are under development.

## Team

University of Florida
Computer Engineering Senior Design

## License

To be determined.
