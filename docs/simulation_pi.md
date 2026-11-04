# Person 5: simulation and Raspberry Pi deployment

Branch: `feature/simulation-pi`. The earlier dashboard is preserved in commit
`174e73a` on `pre-alpha-stakeholder-demo`; this branch inherits it. No UI or database
rewrite is included in Person 5's deliverable.

## What the current simulations represent

The earlier dashboard generates post-CV centres and orientations in two camera
frames. It does not begin with raw images or analog voltages. The separate optical
demo (`python -m fairway_fusion.synthetic`) does render images and runs them through
the image-processing pipeline, but also does not simulate camera electronics.

The team's requested pre-alpha boundary is simpler: a sequence of reconstructed
ball positions through time, fed into launch-angle and ball-speed calculations.
`fairway_fusion.simulation` implements that boundary. Its fixtures are deliberately
labelled simulation. The first version assumes constant velocity across a 5 ms
window and supplies samples at 0, 1, 2, 3, 4 and 5 ms. It does not derive position
from microphone voltage or assign accuracy claims to synthetic measurements.

The expected physical path is:

```text
Conditioned microphone -> Pico ADC or digital threshold -> impact trigger
Pico timing -> exposure / IR flashes -> camera images
Camera sensor ADC + image readout -> digital image data on the Pi
Calibration + ball detection + reconstruction -> timestamped X/Y/Z
Launch calculations -> team server -> SQLite -> results / history UI
```

An extra Pi ADC is not required to convert CSI camera positions. The camera digitizes
image intensities internally; computer vision recovers position. The microphone
may use an ADC or its detector's digital output, depending on the team's wiring.
`--adc` supplies a separate illustrative 12-bit, 3.3 V conditioned microphone signal
with native counts, reconstructed voltage and an impact threshold. Its 50 µs
sampling and threshold are simulation assumptions. It does not drive GPIO, validate
real timing, or feed ball coordinates. Pico MicroPython `read_u16()` has a different
returned scale from the native 0–4095 codes used in this example.

## Run and export

From the repo root, using the existing environment on Windows:

```powershell
.venv\Scripts\python.exe -m fairway_fusion.simulation --scenario straight --analyze
.venv\Scripts\python.exe -m fairway_fusion.simulation --scenario high-launch --output high-launch.json
.venv\Scripts\python.exe -m fairway_fusion.simulation --scenario duplicate-time --output invalid.json
.venv\Scripts\python.exe -m fairway_fusion.simulation --adc --output microphone-adc.json
.venv\Scripts\python.exe scripts/verify_simulation.py
```

Scenarios: straight (50 m/s, 15°), high-launch (35 m/s, 35°), right-launch
(60 m/s, 12°, 4° right). `--noise-mm 0.1 --seed 42` adds reproducible position noise.
Invalid samples cover duplicate time, reversed order, missing coordinates, too few
samples and nonnumeric values. `--analyze` exits 2 on invalid samples.

## Contract for teammates

```json
{
  "schema_version": 1,
  "shot_id": "example",
  "source": "simulation",
  "input_stage": "reconstructed_ball_positions",
  "samples": [
    {"t_ms": 1, "x_mm": 50, "y_mm": 0, "z_mm": 43},
    {"t_ms": 2, "x_mm": 100, "y_mm": 0, "z_mm": 53}
  ]
}
```

Axes: X downrange, Y left, Z up. Increasing times, finite coordinates and at least
two samples are required. Millimetres and milliseconds are explicitly converted to
metres and seconds before calculations. Speed is displacement magnitude over time;
launch angle uses the vertical component relative to the horizontal plane.

Person 4 can call the generator directly when the Simulate Shot button is pressed:

```python
from fairway_fusion.simulation import generate_shot, calculation_input
packet = generate_shot("straight", seed=42)
positions_m, times_s = calculation_input(packet)
# Pass these arrays to Person 1's calculation function, then Person 3's storage API.
```

`analyze_samples(packet)` demonstrates that handoff using the existing repository
calculation functions. The results contain speed_m_s, speed_mph, launch_angle_deg,
launch_direction_deg and velocity_m_s. They do not contain fabricated spin/carry
values: those require orientation observations and a flight model, available in
the preserved richer demo.

Ownership and acceptance:

| Owner | Integration responsibility |
| --- | --- |
| Person 1 | Validated speed/angle calculation API; agree on this sample contract |
| Person 2 | Start/end session, Ready, Simulate Shot, results and history screens |
| Person 3 | Save sessions/shots in local SQLite; retrieve after restart |
| Person 4 | Wire button -> generator -> calculations -> storage -> UI; reject shots without active session |
| Person 5 | Sample/invalid trajectories, SI conversion handoff, Pi setup and verification checklist |

Simulate Shot creates a complete timestamped sequence. Request arrival time must not
replace sample timestamps. The server may animate the samples if desired; generating
them with Python sleep calls would add scheduling jitter without improving this test.

## Raspberry Pi deployment

Use 64-bit Raspberry Pi OS and Python 3.10+. On the Pi:

```sh
sudo apt update
sudo apt install python3-venv python3-pip git
git clone https://github.com/giancarlov0729/fairway-fusion.git
cd fairway-fusion
# After this branch has been pushed:
git switch feature/simulation-pi
sh deploy/pi/install.sh
systemctl --user start fairway-fusion-demo
```

The installer creates a venv, installs the package, verifies simulation/calculation
handoff, and creates a user systemd service. It runs as your normal Pi user and
does not configure camera drivers, ADC wiring, boot overlays or root services.
The service currently starts the PRESERVED dashboard at http://127.0.0.1:8765,
which uses its own richer input schema. Person 4 should change ExecStart to the team
server entry point when that server is integrated; the two sample schemas are not
interchangeable. The simple session/history flow is still owned by Persons 2–4.

```sh
systemctl --user status fairway-fusion-demo
journalctl --user -u fairway-fusion-demo -n 50
.venv/bin/python scripts/verify_simulation.py --server-url http://127.0.0.1:8765
systemctl --user stop fairway-fusion-demo
```

Optional login startup: `systemctl --user enable fairway-fusion-demo`. To view from
a laptop without exposing the service on the network, tunnel with
`ssh -L 8765:127.0.0.1:8765 YOUR_PI_USER@YOUR_PI_HOST` and open localhost on the laptop.
The HTTP verifier checks availability only. Linux/systemd execution and physical Pi
performance have not been verified on this Windows development machine.

## Full-team demo acceptance checklist

After the other branches are integrated, record pass/fail evidence on the Pi:

1. Start a session -> Ready appears and a session ID is created in local storage.
2. Simulate Shot -> generator returns time/position samples -> calculation API runs.
3. Latest result shows calculated speed and launch angle, with simulation provenance.
4. History returns the saved shot in the active session.
5. End session -> simulation is disabled/rejected until another session starts.
6. Restart the server and reopen history -> saved sessions/shots are still present.
7. Inject each invalid fixture -> visible rejection; no successful shot is saved.
8. Repeat offline and verify localhost access, logs and startup on the actual Pi.

These are integration acceptance steps, not claims that teammate session/storage
features have already been implemented. Run `python -m pytest -q` for the automated
simulation, adapter and inherited optical/demo regression checks.
