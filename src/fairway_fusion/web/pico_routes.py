import atexit 
import os  
from flask import jsonify  
from fairway_fusion.hardware.pico_client import PicoClient, PicoError  


def register_pico_routes(app): 

    port = app.config.get("PICO_PORT", os.environ.get("FAIRWAY_PICO_PORT", ""))  # Permit tests to override port
    pico = app.config.get("PICO_CLIENT")                                         # Permit fake client in route tests   

    if pico is None:                    # Create real client when no test client was supplied
        pico = PicoClient(port)             # Construction does not open USB or require connected hardware

    app.extensions["pico"] = pico       # Make client available to future capture orchestration
    atexit.register(pico.close)         # Release USB on notmal process exit

    @app.errorhandler(PicoError)        # Convert expected USB failtures to JSON
    def handle_pico_error(error):  
        return jsonify(error={"code": "pico_unavailable", "message": str(error)}), 503  

    @app.post("/api/pico/connect")      # Establish USB session
    def connect_pico():  
        identity = pico.connect()  
        return jsonify(connected=True, firmware=identity, capture_ready=False) 

    @app.get("/api/pico/status")        #  Read live status from connceted controller
    def pico_status():  
        state = pico.command("STATUS")  
        return jsonify(connected=True, state=state, capture_ready=False)  

    @app.post("/api/pico/disarm")       # Reserve the disarm API 
    def disarm_pico():  
        state = pico.command("DISARM")  
        return jsonify(state=state, capture_ready=False)  

    @app.post("/api/pico/disconnect")   # Release serial device     
    def disconnect_pico():  
        pico.close()  
        return jsonify(connected=False, capture_ready=False)  