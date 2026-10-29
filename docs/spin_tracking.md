# Golf Ball Spin Estimation

## Objective

Estimate the angular velocity and rotation axis of a golf ball using high-speed camera images.

## Marker-Based Tracking

A golf ball will be marked with a non-symmetric arrangement of high-contrast dots.

The cameras will observe the ball during its initial flight.

Marker positions will be detected and associated between observations to estimate the ball's changing orientation.

## Processing Stages

1. Detect the golf ball.
2. Determine its center and apparent radius.
3. Detect visible surface markers.
4. Associate marker observations across frames.
5. Estimate the ball's orientation at different timestamps.
6. Calculate angular velocity.
7. Calculate total spin rate and spin axis.

## Angular Velocity

For rotation about a fixed axis:

omega = delta_theta / delta_t

where:

omega = angular velocity in radians per second
delta_theta = angular displacement in radians
delta_t = elapsed time in seconds

Spin rate in RPM:

RPM = (omega * 60) / (2 * pi)

For general 3D rotation, relative orientation will be represented using rotation matrices or quaternions.

## Challenges

### Motion Blur

High ball velocity can blur the ball and its markers.

Short exposure times and sufficient lighting are required.

### Marker Occlusion

Not every marker will be visible in every frame.

The algorithm must handle partial visibility and changing marker appearance.

### Marker Correspondence

Similar-looking dots can be difficult to distinguish.

An asymmetric marker arrangement can reduce ambiguity.

### Frame Synchronization

Accurate timestamps are necessary to estimate angular velocity.

### Rotation Ambiguity

Multiple ball orientations may explain the same visible marker pattern.

Multiple observations and geometric constraints will help resolve ambiguity.

## Initial Development Strategy

Begin with recorded or synthetic images containing known ball rotations.

Validate the estimated spin against known angular velocities.

Progress to real high-speed camera footage after calibration and detection are reliable.

## Status

Research and algorithm design.
