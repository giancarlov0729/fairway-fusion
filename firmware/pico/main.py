"""MicroPython RP2040 bench firmware. Pin mapping is provisional: see docs/camera_setup.md.

The PIO generates the complete three-flash train without USB/Python timing jitter.
GPIO 2 = logical exposure gate (fan out to BOTH camera interface circuits).
GPIO 3 = logical LED-driver gate. GPIO 4 = conditioned impact signal, active high.
Both outputs active high at the Pico; adapt polarity/voltage in interface circuits.
"""

import sys
import time
import select
import json
import binascii
from machine import Pin
import rp2

BASE = 2
IMPACT = 4


@rp2.asm_pio(set_init=(rp2.PIO.OUT_LOW, rp2.PIO.OUT_LOW))
def train():
    pull(block)  # Initial delay countdown
    mov(x, osr)
    pull(block)  # Gap countdown retained in OSR
    set(y, 2)  # Three pulses
    wait(0, pin, 0)  # Require a new impact edge
    wait(1, pin, 0)
    set(pins, 1)  # Assert exposure gate
    label("delay")
    jmp(x_dec, "delay")
    label("flash")
    set(pins, 3)[7]  # 8 us LED pulse at 1 MHz
    set(pins, 1)
    mov(x, osr)
    label("gap")
    jmp(x_dec, "gap")
    jmp(y_dec, "flash")
    set(pins, 0)  # Both outputs idle
    mov(isr, y)
    push(block)  # Completion notification, not an exposure timestamp
    # Automatic wrap stalls at pull until the next arm.


def send(payload):
    body = json.dumps(payload)
    crc = binascii.crc32(body.encode()) & 0xFFFFFFFF
    sys.stdout.write(body + "\t" + "%08x" % crc + "\n")


def decode(line):
    body, crc = line.strip().rsplit("\t", 1)
    if (binascii.crc32(body.encode()) & 0xFFFFFFFF) != int(crc, 16):
        raise ValueError("CRC mismatch")
    packet = json.loads(body)
    if not isinstance(packet, dict) or not isinstance(packet.get("sequence"), int):
        raise ValueError("Expected an object with integer sequence")
    return packet


def run():
    sm = None
    active = None
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    Pin(BASE, Pin.OUT, value=0)
    Pin(BASE + 1, Pin.OUT, value=0)
    impact = Pin(IMPACT, Pin.IN, Pin.PULL_DOWN)
    try:
        while True:
            if active is not None and sm is not None and sm.rx_fifo():
                sm.get()
                send(
                    {
                        "event": "complete",
                        "sequence": active["sequence"],
                        "pulse_centers_us": [
                            active["delay_us"] + 4 + i * active["spacing_us"]
                            for i in range(3)
                        ],
                        "pulse_widths_us": [8, 8, 8],
                        "verified": False,
                    }
                )
                active = None
                sm.active(0)
                sm = None
            if (
                active is not None
                and time.ticks_diff(time.ticks_ms(), armed_at) > 30000
            ):
                sm.active(0)
                Pin(BASE, Pin.OUT, value=0)
                Pin(BASE + 1, Pin.OUT, value=0)
                send(
                    {
                        "event": "error",
                        "sequence": active["sequence"],
                        "reason": "Impact timeout",
                    }
                )
                active = None
                sm = None
            if not poll.poll(10):
                continue
            packet = None
            try:
                packet = decode(sys.stdin.readline())
                command = packet["command"]
                if command not in ("arm", "disarm"):
                    raise ValueError("Unknown command")
                if command == "arm" and active is not None:
                    raise ValueError("Already armed")
                if sm is not None:
                    sm.active(0)
                Pin(BASE, Pin.OUT, value=0)
                Pin(BASE + 1, Pin.OUT, value=0)
                if command == "disarm":
                    active = None
                    sm = None
                    send({"event": "disarmed", "sequence": packet["sequence"]})
                    continue
                spacing = int(packet["spacing_us"])
                delay = int(packet["delay_us"])
                if not 250 <= spacing <= 2500 or not 10 <= delay <= 100000:
                    raise ValueError("Timing out of range")
                # set gate (1) + delay loop (count+1) -> first pulse at delay.
                # Between pulse onsets: 8 + 1 + 1 + (count+1) + 1 = count+12.
                sm = rp2.StateMachine(
                    0, train, freq=1_000_000, set_base=Pin(BASE), in_base=impact
                )
                sm.put(delay - 2)
                sm.put(spacing - 12)
                active = packet
                armed_at = time.ticks_ms()
                sm.active(1)
                send({"event": "armed", "sequence": packet["sequence"]})
            except (ValueError, KeyError, TypeError) as exc:
                send(
                    {
                        "event": "error",
                        "sequence": packet.get("sequence", -1) if packet else -1,
                        "reason": str(exc),
                    }
                )
    finally:
        if sm is not None:
            sm.active(0)
        Pin(BASE, Pin.OUT, value=0)
        Pin(BASE + 1, Pin.OUT, value=0)


run()
