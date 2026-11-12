import json
import re
import os                   # Read OS name for Linux-only port locking
import time                 
from threading import Lock
from uuid import uuid4
import serial


class PicoError(RuntimeError):  # Connection, timeout, or protocol errors
    pass 

class PicoRejected(PicoError):
    """A correlated command rejection; the transport is still usable."""


class PicoClient:    # Manage 1 connection to the Pico 
    # Define firmware's replies            
    EXPECTED = {"HELLO": "PICO2W_USB_1", "PING": "PONG", "STATUS": "USB_ONLY", "DISARM": "DISARMED"}

    CAPTURE_FIRMWARE = "PICO2W_CAPTURE_1"
    CAPTURE_STATES = {"idle", "configured", "armed", "capturing", "completed", "cancelled", "timed_out", "failed"}

    # Set up a new client
    def __init__(self, port, timeout=2.0, serial_factory=serial.Serial):   
        self.firmware = None
        self.port = port                    # Save serial port name 
        self.timeout = timeout              # Save timeout for Pico responses
        self._factory = serial_factory      # Save function used to open port
        self._serial = None                 # Start w/o open connection
        self._lock = Lock()                 # Create lock to protect connection

    def connect(self):                      # Open port + identify pico 
        with self._lock:                    # Allow 1 thread to use connection at a time
            self._close()                       # Close previous connection    

            if not self.port:                   # Check if port was provided
                raise PicoError("Set FAIRWAY_PICO_PORT before starting Flask.") # Missing port 
                
            try:                                # Attempt connection and handshake
                options = {"exclusive": True} if os.name == "posix" else {}                                                         # Request exclusive access to port on linux
                self._serial = self._factory(port=self.port, baudrate=115200, timeout=0.05, write_timeout=self.timeout, **options)  # Open serial port with Pico's settings
                self._serial.dtr = True                                                                                             # Tell the Pico USB interface the host is connected
                self.firmware = self._exchange("HELLO")
                return self.firmware                                                                                      # Send HELLO, check reply
            
            except (OSError, ValueError, PicoError) as error:   # Connection or response error?
                self._close()                                       # Close port
                raise PicoError(str(error)) from error              # Report error 

    def command(self, command):                                 # Send supported command to Pico 
        if command not in self.EXPECTED:                            # IF command is not supported                                            
            raise ValueError("Supported commands: HELLO, PING, STATUS, DISARM.")    # Reject
        
        with self._lock:                                            # Allow 1 thread to use connection at a time

            if self._serial is None:                                    # Check if connection is open
                raise PicoError("Pico is disconnected; connect it first.")  # Missing connection, report it
            
            try:                                                        # Attempt to send command
                return self._exchange(command)                              # Send command + return checked reply
            
            except (OSError, ValueError, PicoError) as error:           # Catch errors
                self._close()                                               # Close connection
                raise PicoError(str(error)) from error                      # Report error 

    def capture_command(self, command, capture_id, *timing):
        """Use CONFIG, ARM, TEST_TRIGGER or REPORT with a server-generated UUID hex."""
        if not isinstance(capture_id, str) or not re.fullmatch(r"[0-9a-f]{32}", capture_id):
            raise ValueError("Expected a lowercase UUID hex capture ID.")
        expected = {"CONFIG": "CONFIGURED", "ARM": "ARMED", "TEST_TRIGGER": "TRIGGERED", "REPORT": None}
        if command not in expected or len(timing) != (4 if command == "CONFIG" else 0):
            raise ValueError("Invalid capture command or argument count.")
        if any(type(v) is not int or not 0 <= v <= 99999 for v in timing):
            raise ValueError("Timing fields must be unsigned integer microseconds.")
        with self._lock:
            if self.firmware != self.CAPTURE_FIRMWARE:
                raise PicoError("Connect capture firmware first.")
            request = " ".join([command, capture_id, *map(str, timing)])
            try:
                return self._exchange(request, capture_id=capture_id, expected=expected[command])
            except PicoRejected:
                raise
            except (OSError, ValueError, PicoError) as error:
                self._close()
                raise PicoError(str(error)) from error

    def close(self):                    # Public close connection method
        with self._lock:                    # Wait until other operations are done
            self._close()                       # Close connection

    def _close(self):                               # Internal cleanup                            
        self.firmware = None
        connection, self._serial = self._serial, None   # Save old connection + mark it disconnected
        if connection is not None:                      # Is there a connection to close?
            try:                                            # Attempt to release
                connection.close()                              # Close port
            except OSError:                                 # Handle errors w/ unplugged device
                pass                                            # Continue

    def _exchange(self, command, *, capture_id=None, expected=None):                                               # Send message + check response
        request_id = uuid4().hex                                                # Create unique ID as hex text
        message = f"FF1 {request_id} {command}\n".encode("ascii")               # Build message + convert to bytes
    
        if self._serial.write(message) != len(message):                         # Check that every byte was sent
            raise PicoError("Incomplete USB write; command outcome is unknown.")    # Report incomplete send
          
        deadline = time.monotonic() + self.timeout                              # Set latest allowed response time
        response = bytearray()                                                  # Buffer for received bytes
    
        while time.monotonic() < deadline:                                      # Keep reading until time runs out
            chunk = self._serial.read(1)                                            # Read 1 byte
    
            if not chunk:                                                           # If no byte arrived
                continue                                                                # Try again 

            response.extend(chunk)                                                  # Add byte to response
    
            limit = 640 if command.startswith("REPORT ") else 128
            if len(response) > limit:                                                 # Check size limit
                raise PicoError(f"Pico response exceeds {limit} bytes.")                     # Response is oversized
            
            if chunk != b"\n":                                                      # Is response line unfinished?
                continue                                                                # If so keep collecting
    
            fields = response.decode("ascii").strip().split()                       # Bytes -> text + separate its fields
    
            if len(fields) != 4 or fields[:2] != ["FF1", request_id]:               # Check field count, version and ID
                raise PicoError("Invalid or mismatched Pico response.")                 # Reject invalid response
            
            if fields[2] == "ERR":                                                  # Error?
                raise PicoRejected(fields[3])
            if fields[2] != "OK":
                raise PicoError("Unexpected Pico response type.")
            payload = fields[3]
            if command.startswith("REPORT "):
                record = json.loads(payload)
                if (not isinstance(record, dict) or record.get("capture_id") != capture_id
                        or record.get("firmware_version") != self.firmware
                        or record.get("status") not in self.CAPTURE_STATES):
                    raise PicoError("Invalid Pico capture report.")
                return record
            if command == "HELLO":
                allowed = {self.EXPECTED["HELLO"], self.CAPTURE_FIRMWARE}
            elif command == "STATUS" and self.firmware == self.CAPTURE_FIRMWARE:
                allowed = self.CAPTURE_STATES
            else:
                allowed = {expected if capture_id else self.EXPECTED[command]}
            if payload not in allowed:
                raise PicoError("Unexpected Pico firmware response.")
            return fields[3]                                                        # Return reply value
        
        raise PicoError("Pico response timed out; reconnect before retrying.")  # Report that no complete reply arrived
    
    
if __name__ == "__main__": 
    import argparse                                                                                 # Read command line args                                                                                         
    parser = argparse.ArgumentParser(description="Test the Fairway Fusion USB-only Pico firmware.") # Create argument reader
    parser.add_argument("--port", required=True)                                                    # Require serial port argument
    arguments = parser.parse_args()                                                                 # Read suplied arguments
    pico = PicoClient(arguments.port)                                                               # Create client for selected port

    try:                                                # Run communication test
        print("HELLO:", pico.connect())                     # Connect + print firmware identity

        for name in ("PING", "STATUS", "DISARM"):           # Test each remaining command
            print(f"{name}:", pico.command(name))               # Send command + print its reply

    finally:                                                # Run cleanup 
        pico.close()                                            # Close serial connection