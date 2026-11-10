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

Existing JSON session, shot and Pico endpoints remain available for programmatic use.
No camera capture, carry or spin is introduced by this UI conversion.

## Recovery and concurrency

Post/Redirect/Get prevents refresh from repeating a successful save. A bounded set of
single-use form tokens prevents double submission of the same displayed form. Tokens
are process-local: run one process and refresh forms after a restart. Other browsers'
changes become visible on navigation or refresh. No JavaScript timer, background
polling, browser storage or automatic reconnection remains. If the browser reports a
connection error, open Results or History to check whether the action completed.

Saved data resides in SQLite (default src/instance/fairway.db, overridable with
FAIRWAY_DB). Timestamps display UTC explicitly. Laptop and Pi databases are separate.
The response policy includes script-src 'none'.

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
