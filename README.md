# Fairway Fusion

Fairway Fusion is a dual-camera computer vision golf launch monitor developed as a Computer Engineering capstone project at the University of Florida.

The system aims to measure golf ball launch characteristics using high-speed imaging and computer vision techniques.

## Current working demo

The pre-alpha includes a Flask web interface, simulated shot inputs, launch calculations,
and persistent SQLite history. Cameras are not required for this demo.

Flow: browser → Flask API → simulated timestamped X/Y/Z positions → velocity and
launch calculations → SQLite → results and history in the browser.

### Run on Windows (PowerShell)

From the repository root, first-time setup:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install Flask numpy pytest
.\.venv\Scripts\python.exe -m pip install -e .
```

Each time you want to run the demo:

```powershell
.\.venv\Scripts\python.exe -m flask --app fairway_fusion.web.app:create_app run --host 127.0.0.1 --port 5000
```

Open http://localhost:5000. Keep the terminal open; Ctrl+C stops the server.
No reinstall is needed on subsequent runs. If port 5000 is occupied by another
server or SSH tunnel, use `--port 5001` and open http://localhost:5001.

### Run on the Raspberry Pi

From this branch's repository folder, first-time setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install Flask numpy pytest
python -m pip install -e .
```

For subsequent runs:

```bash
source .venv/bin/activate
python -m flask --app fairway_fusion.web.app:create_app run --host 127.0.0.1 --port 5000
```

To use the laptop browser with the Pi server, connect from the laptop using
`ssh -L 5000:127.0.0.1:5000 fairway@fairway-fusion.local`, start Flask in that
SSH session, then open http://localhost:5000 on the laptop. The machines must
have network connectivity to each other. Run only one server process.

### Presentation walkthrough

1. Start a session and choose Straight, High launch, or Right launch.
2. Press Simulate shot. The server calculates and saves the result.
3. Expand View input samples to show the timestamps and positions used.
4. Repeat with another scenario, then end the session.
5. Open History and click a shot to retrieve its saved result and inputs.
6. Refresh or restart Flask: saved history persists. Results restores the newest
   shot in the active session, or the newest saved shot overall if no session is active.

| Scenario | Speed (m/s) | Launch angle | Direction |
| --- | --- | --- | --- |
| Straight | 50 | 15° | 0° |
| High launch | 35 | 35° | 0° |
| Right launch | 60 | 12° | 4° right |

These repeatable scenarios generate positions that are processed by the calculation
code; they do not establish real-world camera accuracy. Positive direction is left;
negative direction is right. Input tables use seconds and meters.

### Storage, recovery, and limits

- Default database: `src/instance/fairway.db` in an editable install. `FAIRWAY_DB`
  overrides it. Laptop and Pi have separate databases unless deliberately copied.
- Requests time out after 10 seconds. Reconnect restores server state; saves are
  never automatically retried. A timed-out action may still have completed, so
  check History before repeating it.
- History displays the latest 50 sessions and up to 50 shots per session.
- Spin, carry, total distance, camera capture, and shot scoring are not implemented
  in this demo. No camera event stream is connected.
- All UI assets are local; no internet is needed after installing dependencies.

### Tests and main files

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

On the Pi, use `python -m pytest -q` with the virtual environment activated.

- `src/fairway_fusion/web/`: Flask routes, HTML template, JavaScript, and CSS.
- `src/fairway_fusion/services/shot_service.py`: coordinates simulation, analysis, and saving.
- `src/fairway_fusion/simulation.py`: simulated position samples.
- `src/fairway_fusion/analysis/`: velocity and launch metrics.
- `src/fairway_fusion/storage/`: SQLite sessions and shot records.
- `tests/`: automated checks.

See [UI setup](docs/ui.md), [API contract](docs/server_api.md), and
[storage documentation](docs/storage.md). The objectives below describe the
larger project, including future hardware work.

## Stereo vision prototype

From the repository root, activate the existing virtual environment.
If you need one, create it with `python -m venv .venv` first.

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
python scripts/analyze_shot.py
python scripts/analyze_shot.py --missing-frame
python -m pytest -q tests/test_vision_pipeline.py
```

The first demo prints a launch result from two synthetic monochrome images.
The second demonstrates a rejected capture. Both run without cameras or Flask.

Save annotated camera views and example payloads for integration:

```bash
python scripts/analyze_shot.py --overlay accepted.png > accepted.json
python scripts/analyze_shot.py --case shallow --overlay rejected.png > rejected.json
```

The numbered circles show the accepted flash matches across both cameras.
Crosses mark detected centers. Rejected captures show the reason and any
available detections. The synthetic cases establish this baseline:

| Case | Expected result |
| --- | --- |
| `clean` | Accepted, with rightward direction |
| `leftward` | Accepted, with leftward direction |
| `blur` | Accepted with mild simulated blur |
| `uneven-light` | Accepted with a dimmer second flash |
| `shallow` | Rejected when both stereo pairings fit |
| `clutter` | Rejected when a third round object is detected |
| `--missing-frame` | Rejected when camera B is missing |

`analyze_stereo_shot` in `pipeline.py` takes two 8-bit monochrome images,
their camera calibrations, and the optical flash interval in seconds. It
returns positions in meters, relative timestamps, launch metrics, and
reprojection errors on success, or a reason on rejection. The detector
expects two isolated bright ball silhouettes per view. Flash ordering
assumes forward motion. See [calibration](docs/calibration.md) for the camera model.

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
