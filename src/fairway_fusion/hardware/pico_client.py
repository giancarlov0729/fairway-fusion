import os                   # Read OS name for Linux-only port locking
import time                 
from threading import Lock
from uuid import uuid4
import serial


class PicoError(RuntimeError):  # Connection, timeout, or protocol errors
    pass 

class PicoClient:    # Manage 1 connection to the Pico 
    # Define firmware's replies            
    EXPECTED = {"HELLO": "PICO2W_USB_1", "PING": "PONG", "STATUS": "USB_ONLY", "DISARM": "DISARMED"}

    # Set up a new client
    def __init__(self, port, timeout=2.0, serial_factory=serial.Serial):   
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
                return self._exchange("HELLO")                                                                                      # Send HELLO, check reply
            
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

    def close(self):                    # Public close connection method
        with self._lock:                    # Wait until other operations are done
            self._close()                       # Close connection

    def _close(self):                               # Internal cleanup                            
        connection, self._serial = self._serial, None   # Save old connection + mark it disconnected
        if connection is not None:                      # Is there a connection to close?
            try:                                            # Attempt to release
                connection.close()                              # Close port
            except OSError:                                 # Handle errors w/ unplugged device
                pass                                            # Continue

    def _exchange(self, command):                                               # Send message + check response
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
    
            if len(response) > 128:                                                 # Check size limit
                raise PicoError("Pico response exceeds 128 bytes.")                     # Response is oversized
            
            if chunk != b"\n":                                                      # Is response line unfinished?
                continue                                                                # If so keep collecting
    
            fields = response.decode("ascii").strip().split()                       # Bytes -> text + separate its fields
    
            if len(fields) != 4 or fields[:2] != ["FF1", request_id]:               # Check field count, version and ID
                raise PicoError("Invalid or mismatched Pico response.")                 # Reject invalid response
            
            if fields[2] == "ERR":                                                  # Error?
                raise PicoError(f"Pico rejected {command}: {fields[3]}")                # Report error
             
            if fields[2] != "OK" or fields[3] != self.EXPECTED[command]:            # Check success flag + reply value
                raise PicoError("Unexpected Pico firmware response.")                   # Report no complete reply arrived
    
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