# LED driver verification — November 10, 2026

Status: design review in progress. This is not an electrical test certificate or permission to apply high LED current. Physical board revision, fabrication capability, component receipt and lab power supply are not yet confirmed.

## Build baseline

Use the PiTrac V3 Connector driver board and **IRLED2**, not the original IRLED footprint. The two boards have different fabrication/assembly needs: the driver is through-hole; the emitters require surface-mount assembly and appropriate thermal contact. Our Pico-controlled adaptation is not the unmodified PiTrac Raspberry Pi GPIO implementation.

- Official assembly: https://docs.pitrac.org/hardware/pcb-assembly/
- Parts: https://docs.pitrac.org/hardware/parts-list/
- Machine-readable population BOM: https://raw.githubusercontent.com/PiTracLM/PiTrac/main/Hardware/Assembly%20Files/V3%20Connector%20Only%20PCB/V3%20Connector%20Only%20Assembly/V3%20Connector%20Only%20Bill%20of%20Materials.csv
- Schematic reviewed previously: https://github.com/PiTracLM/PiTrac/blob/main/Hardware/Schematic/V3%20Connector%20%2B%20IRLED2%20Schematic.pdf

Archive a single matching revision of schematic, layout, fabrication outputs and population BOM before building. Record commit/version and all substitutions. Do not combine revisions based on filenames alone.

## Substitution review

### TC4420CPA replacing MCP1407-E/P

Microchip explicitly describes the MCP1406/07 as pin-compatible with TC4420/29. TC4420 and MCP1407 are non-inverting. Their PDIP-8 connections match: 1/8 VDD, 2 INPUT, 3 NC, 4/5 GND, 6/7 OUTPUT. All duplicated supply, ground and output pins must be connected as specified. Operating supply range is 4.5–18 V. The replacement has different delay/drive characteristics; actual gate and LED-current pulse waveforms still need measurement.

- TC4420: https://ww1.microchip.com/downloads/en/DeviceDoc/21419D.pdf
- MCP1407: https://ww1.microchip.com/downloads/en/DeviceDoc/20002019C.pdf

### MC34063ABN replacing MC33063AP

The ST part has the expected DIP-8 regulator functions: switch collector 1, switch emitter 2, timing capacitor 3, ground 4, comparator input 5, supply 6, peak-current sense 7, driver collector 8. Verify the original supplier's MC33063AP electrical limits against the fitted circuit before closing this review; matching functions alone does not verify current limit, oscillator tolerances, temperature range or thermal behavior.

- ST: https://www.st.com/resource/en/datasheet/mc34063ab.pdf

### LEDs

VSMA1085400 is an 850 nm surface-mount emitter. Vishay lists 1.5 A forward current and 5 A surge at 100 microseconds under its stated conditions. These are limits, not recommended prototype settings. The two parallel five-LED strings do not guarantee equal current sharing. Do not use the schematic's high-current annotations as approved settings. Start current selection only after pulse width, repetition, thermal mounting and branch behavior are reviewed.

- Vishay: https://www.vishay.com/docs/80294/vsma1085400.pdf

## Strobe path and control interface

The previously reviewed sheet 4 marks MOSI-path R20/D3 unpopulated; D4 connects DIAG to the gate driver. Verify the actual layout and population before choosing the Pico flash signal. If a MOSI-to-strobe branch is populated, DAC/ADC SPI traffic could interact with lighting control. DIAG is a control input, not an independent hardware power inhibit.

The current Pico USB-only firmware does not establish electrical output states, set DAC current or operate this board. Before connecting it: document GPIO assignments, logic supply rails, SPI connections, boot/off pull resistors, firmware pulse limits and a separate way to remove LED power. Do not apply the elevated gate-drive or boost voltage to Pico GPIO. Keep the TC4427 camera buffer distinct from the TC4420 LED MOSFET driver.

## Additional inventory to verify

The user-reported specialized-parts order does not cover the entire reference BOM. These parts require a lab-stock/ordering check:

- C2/C11: two 1 uF capacitors, correct voltage, dielectric and lead spacing.
- C3/C8/C10/C12/C14/C15: six 100 nF capacitors, correct voltage and dielectric.
- C4: 470 pF timing capacitor, C0G/NP0.
- C5: 1 uF, 100 V class reference part.
- C7: 100 nF, 100 V class reference part.
- C9: 4.7 uF ceramic, matching reference constraints.
- D1 power Schottky and D4 signal Schottky, correct ratings and orientation.
- Q4: 2N3904, verify physical pinout of the supplied manufacturer/package.
- All other reference resistors, including precision divider/control values; do not substitute approximate lab values without recalculating the affected function.
- Indicator LEDs and their resistors, headers, terminals, solder/reflow supplies, mounting and insulation.
- Connectors used in the actual build; a deliberate connector omission must be recorded and its replacement wired explicitly.

Follow the matching machine-readable BOM for reference quantities. The web parts table currently contains inconsistent text for D1; do not read its quantity as an instruction to populate extra components. Check the actual schematic and layout.

## Bench acceptance sequence

1. Inspect unpowered: component values and orientation, solder bridges, continuity, grounds and absence of supply shorts. Verify board traces/vias and LED thermal pads.
2. Keep Pi/Pico and LEDs disconnected initially. Verify the documented input supply and current-limited bench setup for the exact board revision.
3. Check supply rails and off-state behavior. Investigate unexpected current, voltage or heating before proceeding. Safely discharge the reservoir before handling or changing wiring.
4. Verify DAC and ADC scaling with safe loads and calibrated measurements. Confirm current command starts at zero and the flash input remains off through boot and reconnect.
5. Check finite low-energy control pulses, MOSFET gate waveform and current-sense signal. Validate the current-sense resistor's scaling, parasitics and scope grounding; never attach a grounded scope clip to an arbitrary switching node.
6. Mount and connect LEDs only after prior checks. Establish conservative pulse conditions and measure actual current, overshoot, pulse width, branch behavior and optical output with suitable equipment. Avoid direct viewing of powered high-intensity IR emitters.
7. Validate fault behavior: held-high input, software timeout, disconnect, reset and stalled command path. Confirm an independent way to stop LED power. Do not deliberately create a destructive condition to test a limit.
8. Only then integrate camera exposures and measure optical flash timing. Save scope evidence and actual settings rather than labelling commanded values as measured.

Record each measurement with board revision, part substitutions, supply, firmware revision, equipment, setting and waveform. Electrical pulse validation does not establish sufficient image brightness or full-speed shot accuracy; those require camera tests afterward.
