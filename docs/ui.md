# JavaScript-free UI

Flask/Jinja renders HTML on the server. The browser uses links, forms, CSS, and native
`details` elements. No JavaScript or JavaScript-dependent framework is used. The
original visual design was adapted from Steffano's UI; existing CSS is retained.

## Run

From the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe -m flask --app fairway_fusion.web.app:create_app run --host 127.0.0.1 --port 5000 --no-reload
```

On the Pi, activate `.venv` and substitute `python`. Open http://127.0.0.1:5000
locally or through the existing SSH tunnel. Python dependencies are in requirements.txt.

## Routes and behavior

- GET /: active session, start/end forms, scenario selection and simulated shot form.
- GET /results: latest shot in the active session, or latest overall without one.
- GET /results/<id>: persistent saved result, provenance and expandable input samples.
- GET /history: latest 50 shots; optional session_id filter and latest 50 sessions.
- POST /actions/start, /actions/end, /actions/simulate: HTML form actions.

The simulator and saved shot history remain available alongside Live Capture.

## Recovery and concurrency

Post/Redirect/Get prevents refresh from repeating a successful save. A bounded set of
single-use form tokens prevents double submission of the same displayed form. Tokens
are process-local: run one process and refresh forms after a restart. Other browsers'
changes become visible on navigation or refresh. Live Capture refreshes its HTML
every two seconds during calibration, arming, and recording. If the browser reports a
connection error, open Results or History to check whether the action completed.

Saved data resides in SQLite (default src/instance/fairway.db, overridable with
FAIRWAY_DB). Timestamps display UTC explicitly. Laptop and Pi databases are separate.
The response policy includes script-src 'none'.

## Live capture on the Pi

Use the Pi camera environment from [camera bring-up](camera_first_demo.md).
Close the desktop camera bench and leave Pico outputs GP2, GP3, and GP4
disconnected from the cameras and IR board. This workflow records free-running
camera footage. The Pico's timing outputs still run during the attempt.

```bash
source .venv-bench/bin/activate
ls /dev/serial/by-id/
export FAIRWAY_PICO_PORT=/dev/serial/by-id/REPLACE_WITH_YOUR_PICO
export FAIRWAY_LIVE_CAPTURE=1
python -m flask --app fairway_fusion.web.app:create_app run --host 127.0.0.1 --port 5000 --no-reload
```

Install Flask in that environment if it is missing. Open **Live Capture** at
`/live` on the Pi or through the SSH tunnel. Set `FAIRWAY_DB` to share a
specific database with the desktop bench. Set `FAIRWAY_STEREO_PROFILE` to a
measured 640×480 hitting-zone profile to enable physical position verification.
The default checks ball appearance in both views.

1. Start a session and open the camera preview. For speed and elevation estimates,
   enter the four pixel corners and measured dimensions of a side-plane rectangle.
   Repeat side-plane setup if the camera moves or the server restarts.
2. Remove the calibration target and ball. Click **Calibrate** to scan the empty
   hitting area. Place one ball inside the outlined areas and click **Arm**.
3. Wait for **Arm successful**. Hit during the Pico's countdown or use
   **Cancel / disarm**. Use **Refresh status** to check progress on demand.
4. The latest attempt appears below the controls with a playable preview when
   supported and a provisional speed and elevation estimate when the fit passes.
   **Recordings** lists saved attempts by session. Its frame browser can show all
   images, ball detections, or only the flight frames used for the calculation.
   The overlay is rendered separately so the original PNG stays available.

Flask operates the cameras and Pico through one capture worker in this mode.
The separate USB controls are unavailable while this mode is enabled. The
saved previews use native browser video and the frame browser works independently
of WebM encoder availability.

## Implementation

- web/page_routes.py: rendering, form processing, single-use tokens and script policy.
- web/templates/index.html: complete server-rendered screens and forms.
- web/static/styles.css: shared responsive styling.
- tests/test_ui.py: forms, persistence, error handling and absence of script assets.

## Manual acceptance

Disable JavaScript in the browser. Start a session, select each scenario, save a shot,
expand input samples, refresh the result, filter history and open an older shot. End
the session and restart Flask; saved results should remain. Repeat a submitted form:
it must not create another shot. Unavailable server recovery uses browser navigation.
