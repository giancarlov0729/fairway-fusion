from fairway_fusion.web.page_routes import register_page_routes

# Flask creates server, jsonify builds JSON responses, request handles incoming requests, render_template serves HTML
from flask import Flask, jsonify, request, render_template

# HTTPException represents errors like 404 Not Found, 500 Internal Server Error, etc.
from werkzeug.exceptions import HTTPException

# Proviides HTTP 400 Bad Request
from werkzeug.exceptions import BadRequest

# Coordinates calculations 
from fairway_fusion.services.shot_service import preview_shot
from fairway_fusion.web.session_routes import register_session_routes

# USB endpoints
from fairway_fusion.web.pico_routes import register_pico_routes
from fairway_fusion.services.capture_service import CaptureService
from fairway_fusion.web.capture_routes import register_capture_routes
from fairway_fusion.hardware.mock_pico import MockPico
from fairway_fusion.web.vision_routes import register_vision_routes

# Create Flask application instance
def create_app(test_config=None):

    app = Flask(__name__)                               # Look for future HTML in web/templates and CSS/JS in web/static

    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024      # Limit request size to 1 MiB

    if test_config is not None:                         # Tests can override config w/o changing normal settings
        app.config.update(test_config)

    @app.get("/")                                       
    def index():

        # Browser displays the integrated session, results, and history interface.
        return app.extensions["render_page"]()

    @app.get("/api/health")
    def health():

        # This reports server availability, not camera or database readiness.
        return jsonify(
            status="ok",
            service="fairway-fusion",
            api_version=1,
        )

    @app.errorhandler(HTTPException)
    def handle_http_error(error):

        # Preserve the original HTTP status and headers.
        # For example, a 405 response retains its Allow header.
        response = error.get_response()

        # Replace the default HTML error page with JSON.
        response.set_data(
            app.json.dumps(
                {
                    "error": {
                        "code": error.name.lower().replace(" ", "_"),
                        "message": error.description,
                    }
                }
            )
        )

        # Tell the browser that the response contains JSON.
        response.content_type = "application/json"

        # Return the original status with our new response body.
        return response

    @app.errorhandler(500)
    def handle_internal_error(error):

        # Flask logs the underlying exception in the server terminal.
        # Keep internal traceback details out of the browser response.
        return jsonify(
            error={
                "code": "internal_server_error",
                "message": "An unexpected server error occurred.",
            }
        ), 500

    @app.post("/api/shots/preview")                                 # Register a POST endpoint for unsaved previews
    def preview():
        payload = request.get_json()                                        # Parse JSON data sent with request

        if not isinstance(payload, dict):                                   # Checks whether payload is a dictionary (JSON object)
            raise BadRequest("The request body must be a JSON object.")

        scenario = payload.get("scenario", "straight")                      # Gets the value of scenario from payload, defaulting to straight

        try:                                                                # Convert expected input-validation failures into HTTP 400 responses
            result = preview_shot(scenario)                                     # Generate observations and calculate metrics
        except ValueError as error:                                         # Catch validation failures from the workflow
            raise BadRequest(str(error)) from error                             # Use our existing JSON error handler

        return jsonify(result)                                              # Send the calculated preview back to the browser

    register_session_routes(app)
    register_page_routes(app)
    register_pico_routes(app)           # Register USB endpoints using 1 shared Pico client
    store = app.extensions["shot_store"]
    store.mark_interrupted_captures()
    app.extensions["capture_service"] = CaptureService(
        app.extensions["pico"], store.captures
    )
    register_capture_routes(app)
    app.extensions["mock_capture_service"] = CaptureService(
        MockPico(), store.captures, source="simulation"
    )
    register_vision_routes(app)

    # Return the configured app to Flask or the test runner.
    return app 
