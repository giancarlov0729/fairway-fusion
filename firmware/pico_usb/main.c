#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/stdio_usb.h"

// Handle a complete message 
static void handle_line(const char *line) {

    char version[8], id[33], command[24], extra[2];                                 // Space for message fields

    int count = sscanf(line, "%7s %32s %23s %1s", version, id, command, extra);     // Split message into fields 

    if(count != 3 || strcmp(version, "FF1") != 0) {                                 // Must have 3 fields and version FF1
        return;                                                                         // Ignore if invalid
    }

    if(strlen(id) != 32 || strspn(id, "0123456789abcdef") != 32) {                  // Check that ID has 32 hex characters
        return;                                                                         // Ignore if invalid
    }

    const char *result = NULL;                                                      // Start w/ no reply selected

    if(strcmp(command, "HELLO") == 0) {             // Check for HELLO                                       
        result = "PICO2W_USB_1";                        // Reply w/ firmware name

    } else if(strcmp(command, "PING") == 0) {       // Check for PING
        result = "PONG";                                // Confirm communication 

    } else if(strcmp(command, "STATUS") == 0) {     // Check for STATUS
        result = "USB_ONLY";                            // Report that USB communication is implemented

    } else if(strcmp(command, "DISARM") == 0) {     // Check for DISARM
        result = "DISARMED";                            // Acknowledge

    }

    if(result != NULL) {                            // If command was recognized
        printf("FF1 %s OK %s\n", id, result);           // Send successful reply w/ same ID

    } else {                                        // Unknown command?
        printf("FF1 %s ERR UNSUPPORTED\n", id);         // Error reply

    }

    fflush(stdout);                                 // Send any buffered reply text
}

// Pico program
int main(void) {
    stdio_init_all();               // Start configured USB input + output

    char line[128];                 // Store incoming message
    size_t used = 0;                // Count stored chars
    bool dropping = false;          // Track if message should be ignored
    uint64_t last_byte = 0;         // Store when last char got here

    while (true) {                                      // On loop...

        if(!stdio_usb_connected()) {                        // USB is disconnected?
            used = 0;                                           // Clear unfinished message
            dropping = false;                                   // Reset ignore flag
            sleep_ms(10);                                       // Wait 10 ms
            continue;                                           // Restart 
        }

        if((used > 0 || dropping) && time_us_64() - last_byte > 1000000) {  // Clear unfinished input afer 1 sec w/o data
            used = 0;                                                           // Clear stored chars
            dropping = false;                                                   // Allow new message
        }

        int ch = getchar_timeout_us(1000);          // Wait up to 1 ms for a char              

        if(ch == PICO_ERROR_TIMEOUT) {              // Check if nothing arrived
            continue;
        }

        last_byte = time_us_64();                   // Save characters arrival time

        if(ch == '\r') {                            // Check for carriage return
            continue;
        }

        if(ch == '\n') {                            // Check for new line

            if(!dropping && used > 0) {                 // Process non empty message that passed input checks
                line[used] = '\0';                          // Mark end of c string
                handle_line(line);                          // Process command and reply
            }

            used = 0;                                   // Clear message buffer position
            dropping = false;                           // Reset for next message

        } else if(ch < 32 || ch > 126 || used >= sizeof(line) - 1) {    // Invalid char or full buffer?
            dropping = true;                                                // Ignore rest of message

        } else if(!dropping) {                                          // Still accepting chars?
            line[used++] = (char)ch;                                        // Store character and increase count

        }

    }

}
