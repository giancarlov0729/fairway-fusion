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

