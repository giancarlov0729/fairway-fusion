# Integrated UI

The visual design and HTML shell were adapted from Steffano's UI branch:
https://github.com/steffanocornejo/fairway-fusion/tree/003bc31aa055057559d7aea208fb9f352fd49bc3/ui

Flask serves the UI and API together. No separate UI server or npm install is needed.

## Run on Windows

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m flask --app fairway_fusion.web.app:create_app run --host 127.0.0.1 --port 5000
```

Open http://127.0.0.1:5000. Stop an existing Flask server or SSH tunnel using port 5000 first, or use `--port 5001` and open that port instead.

For a fresh checkout, create `.venv` with `py -m venv .venv`, then install `Flask numpy pytest` using `.\.venv\Scripts\python.exe -m pip install Flask numpy pytest` before the commands above.

## Demo

1. Start a session.
2. Select Straight, High launch, or Right launch and press Simulate shot.
3. Results show calculated speed, launch angle, and direction, with the saved shot ID.
4. Choose Next shot to simulate another shot.
5. Reload during an active session: the active session and saved shot count are restored.
6. End session, open History, and select a shot to see its saved results.
7. Restart Flask and revisit History to demonstrate persistence.

The scenarios use deterministic position samples. Repeating a scenario gives the same metrics but creates a new saved shot. Right launch should display 4.0 degrees right. Positive direction means left, negative means right.

History shows the latest 50 sessions and latest 50 shots per session, including active and empty sessions. Rejected records show their reason and unavailable metrics as a dash.

## Files and data

- `src/fairway_fusion/web/templates/index.html`: page shell.
- `src/fairway_fusion/web/static/styles.css`: teammate's styling plus responsive adjustments.
- `src/fairway_fusion/web/static/app.js`: screens, button actions, session recovery, and error display.
- `src/fairway_fusion/web/static/api.js`: actual HTTP requests matching the existing Flask API.
- `src/fairway_fusion/web/app.py`: serves the page at `/`.

All saved data comes from SQLite through Flask. Browser mock data and localStorage are no longer used. The default database is `src/instance/fairway.db`; `FAIRWAY_DB` can override it. Laptop and Pi databases are separate unless deliberately copied.

This is a simulation demo with no camera capture or shot stream. Spin, carry, total distance, and shot quality scoring are not implemented; the UI does not invent values for them. No internet connection or external assets are required after installation.

On the Pi, activate its virtual environment and use the same Flask command with `python`. The existing SSH tunnel allows the laptop to open `http://127.0.0.1:5000` while Flask runs on the Pi.

## Demo refinements

Requests have a 10-second timeout, including reading the response body. Connection
failures unlock the UI and offer Reconnect. Reconnect reads state without repeating
writes; check History before retrying a save that may already have completed.

On startup or reconnect, Results loads the latest shot in the active session, or
latest overall if there is no active session. An empty active session has no result.
This restores the latest saved result, not an older history selection or the selected tab.

Results offers an expandable timestamp/X/Y/Z table from stored samples. History
uses separate time, speed, angle, direction, and status columns; narrow screens
can scroll the table horizontally.
