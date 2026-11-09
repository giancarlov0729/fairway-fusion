# Official Raspberry Pi hardware references

Downloaded unchanged from Raspberry Pi on November 9, 2026. Preserve the source PDFs and their included notices. These documents establish hardware and SDK behaviour, not the team's custom FF1 protocol.

| Local reference | Official source | Use |
| --- | --- | --- |
| [Pi 5 product brief](raspberry-pi-5-product-brief.pdf) | https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-product-brief.pdf | Host USB connectors, power and interface overview |
| [Pico 2 W datasheet](pico-2-w-datasheet.pdf) | https://datasheets.raspberrypi.com/picow/pico-2-w-datasheet.pdf | Correct board: RP2350, pinout, micro-USB, power connections |
| [RP2350 datasheet](rp2350-datasheet.pdf) | https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf | Microcontroller USB, timers, PIO and electrical limits |
| [Pico C/C++ SDK](raspberry-pi-pico-c-sdk.pdf) | https://datasheets.raspberrypi.com/pico/raspberry-pi-pico-c-sdk.pdf | Firmware build, pico_stdio_usb and hardware APIs |

Additional authoritative references:

- USB stdio API: https://www.raspberrypi.com/documentation/pico-sdk/runtime.html
- Official USB example: https://github.com/raspberrypi/pico-examples/tree/master/hello_world/usb
- SDK installation: https://github.com/raspberrypi/pico-sdk
- USB stdio implementation/configuration: https://github.com/raspberrypi/pico-sdk/blob/master/src/rp2_common/pico_stdio_usb/include/pico/stdio_usb.h
- Pi power limits: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- pySerial API: https://pyserial.readthedocs.io/en/latest/pyserial_api.html

For this USB-only test, connect the Pico's micro-USB to a Pi USB-A port with a data cable. Keep the Pi powered through its USB-C supply. USB provides Pico power and data; the Pi USB-C power socket is not this project's host-data connection. A Pi 5 using a 3 A supply normally limits total peripheral USB current to 600 mA; account for the touchscreen and other peripherals as the system grows.

Our Pico is a **Pico 2 W**, not an RP2040 Pico. Select `PICO_BOARD=pico2_w`. Use a Pico SDK release that supports that board, with its required submodules initialized. USB CDC firmware must be flashed before a useful serial command session exists; BOOTSEL mass storage is a different mode.

See [the custom USB contract](../pico_usb_protocol.md) for application-specific commands.
