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
                self._serial.dtr = True                                                                                             # Set DTR to true to reset Pico + start firmware
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

    