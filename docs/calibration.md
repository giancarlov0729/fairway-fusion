# Camera Calibration

## Purpose

Camera calibration establishes the relationship between image coordinates and physical space.

Accurate calibration is required for stereo reconstruction and launch measurements.

## Intrinsic Calibration

Each camera will be calibrated to estimate:

- Focal lengths
- Principal point
- Lens distortion coefficients

OpenCV calibration tools will be used with a known calibration target.

## Stereo Calibration

Stereo calibration determines the relative position and orientation of the cameras.

Required outputs include:

- Rotation matrix
- Translation vector
- Stereo projection matrices

## 3D Reconstruction

Corresponding observations from the two cameras will be used to triangulate the golf ball's position.

The reconstructed trajectory will be expressed in a consistent physical coordinate system.

## Coordinate System

The project will establish a common coordinate system defining:

- Forward ball flight
- Horizontal direction
- Vertical direction

The exact axis convention will be finalized before implementing trajectory calculations.

## Validation

Calibration quality will be evaluated using reprojection error and reconstruction of objects at known positions.

Calibration parameters must be updated whenever camera positions, lenses, focus, or other relevant optical settings change.

## Status

Calibration implementation pending.
