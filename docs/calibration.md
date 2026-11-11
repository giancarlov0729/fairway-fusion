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

The golf coordinate frame uses meters. +X points downrange, +Y left, and +Z up.

## Validation

Calibration quality will be evaluated using reprojection error and reconstruction of objects at known positions.

Calibration parameters must be updated whenever camera positions, lenses, focus, or other relevant optical settings change.

## Status

The vision pipeline accepts an OpenCV pinhole model for each camera.
Supply its intrinsic matrix, distortion coefficients, world-to-camera
rotation and translation, and calibrated image size. The pipeline undistorts
detected centers and triangulates the matched observations.

The laptop fixture supplies synthetic calibration. Once the physical cameras
are mounted and focused, use paired target images to fit the lens and stereo
parameters, align them to the golf coordinate frame, and verify independent
distances and heights. Fitting parameters from target images is a follow-up.
