# Fairway Fusion Software Architecture

## Overview

Fairway Fusion uses a dual-camera computer vision system to estimate golf ball launch conditions.

The software is divided into independent modules to simplify development, testing, and hardware integration.

## Processing Pipeline

Camera 1 + Camera 2
        |
        v
Frame Capture
        |
        v
Frame Synchronization
        |
        v
Image Undistortion
        |
        v
Ball Detection
        |
        v
Ball Tracking
        |
        +-------------------+
        |                   |
        v                   v
3D Reconstruction      Marker Detection
        |                   |
        v                   v
Velocity Estimation    Rotation Estimation
        |                   |
        v                   v
Launch Metrics         Spin Metrics
        |                   |
        +---------+---------+
                  |
                  v
             Shot Validation
                  |
                  v
              Shot Results

## Module Responsibilities

### Cameras

Responsible for capturing frames, timestamps, and synchronization information.

### Calibration

Responsible for camera intrinsic parameters, lens distortion correction, and stereo calibration.

### Detection

Identifies golf balls and visible surface markers in camera images.

### Tracking

Associates detected balls and markers across frames.

### Analysis

Calculates velocity, launch angle, launch direction, spin rate, and spin axis.

### Pipeline

Coordinates the processing stages and produces shot results.

## Design Principles

- Modular components
- Hardware-independent processing algorithms
- Offline testing before live camera integration
- Automated unit testing
- Configurable camera parameters
- Separation of image processing and physical calculations

## Current Status

Architecture defined. Implementation pending.
