# Camera System

## Overview

Fairway Fusion uses two cameras to observe a golf ball immediately after impact.

The camera system must support accurate ball localization, trajectory reconstruction, and marker-based spin estimation.

## Hardware Requirements

Camera selection should consider:

- Resolution
- Frame rate
- Global versus rolling shutter
- Exposure time
- Synchronization capabilities
- Lens distortion
- Field of view
- Interface bandwidth
- Lighting requirements

## Camera Placement

The cameras must provide overlapping views of the measurement region.

Their relative positions and orientations must be calibrated.

Final camera placement will depend on the selected hardware, available space, and desired measurement accuracy.

## Synchronization

The system must associate observations from both cameras with accurate timestamps.

Hardware synchronization is preferred when supported.

Software synchronization may be evaluated experimentally.

## Calibration

Each camera requires intrinsic calibration.

The stereo system additionally requires extrinsic calibration.

Calibration parameters include:

- Camera intrinsic matrices
- Lens distortion coefficients
- Relative camera rotation
- Relative camera translation

## Lighting

Short exposure times require sufficient illumination.

Lighting should minimize reflections, motion blur, and shadows that interfere with marker detection.

## Validation

Camera performance will be evaluated using:

- Calibration reprojection error
- Ball detection reliability
- Stereo reconstruction accuracy
- Timestamp alignment
- Marker visibility
- Repeatability of measurements

## Hardware Status

Camera models and final mounting geometry have not been selected.
