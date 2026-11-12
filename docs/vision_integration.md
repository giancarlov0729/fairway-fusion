# Vision integration work chunks

Work on `feature/vision-integration`. Each chunk is reviewed and tested separately;
commit only after Ethan explicitly says "commit".

1. Vision-service adapter: image pair + calibration + flash interval -> accepted
   or rejected result, with launch metrics and optional carry. No storage or UI.
2. Save synthetic captures: separate A/B PNGs, synthetic calibration, exact timing,
   session ID and image paths. Reload the saved inputs and reproduce the result.
3. Process and store captures: save accepted/rejected shots linked to captures,
   preserve diagnostic evidence, distinguish processing failures, prevent duplicates.
4. Flask workflow: create synthetic image attempts and process saved captures;
   validate sessions/inputs and keep physical and synthetic sources separate.
5. UI: server-rendered vision demo, actual generated images, overlays, processing
   results and links to Shot Analysis. No JavaScript.
6. Failure/replay validation: missing frames, blur, lighting, clutter, ambiguous
   matches, restarts, provenance guards, repeatable demo and full regression suite.

## Chunk 1 interface

`fairway_fusion.services.vision_service.analyze_image_pair` takes Camera A's image,
Camera B's image, Camera A's `CameraCalibration`, Camera B's `CameraCalibration`,
and a required `flash_interval_s`. Images must be calibrated-size uint8 monochrome
NumPy arrays. Coordinates are metres: +X downrange, +Y left, +Z up.

Keyword `source` is required: `simulation`, `live`, or `replay`. `timing_source`
defaults to `commanded`; use `measured` only with independently verified timing.
These labels are caller declarations, not proof of provenance. Calibration source
and stored-record linkage will be checked by later storage integration.

There is no default flash interval: Giancarlo's fixture demonstration uses 0.002 s;
the Pico example configuration uses 0.001 s. Always pass the interval for the
particular image pair. This adapter neither reads Pico reports nor creates images.

Every result contains status, reason, source, timing, samples, launch, carry,
reprojection_errors_px and diagnostics. Rejected results have no measurements or
carry, but retain available detections. Accepted results include positions/times in
`samples`, the pipeline's unchanged `launch` metrics, and a separate carry block.
A carry-model failure does not invalidate the measured launch; its numbers are
empty and `carry_error` explains why. Optional `carry_assumptions` accepts the
existing carry model's `Assumptions` dataclass.

Known invalid input returns a rejection. Invalid source/settings raise a clear
exception. Unexpected pipeline exceptions propagate for the future processing
worker to classify as failures, rather than disguising bugs as rejected shots.
Returned results use JSON-compatible Python types. Inputs are not modified.

Camera validation checks shapes, finite values, positive focal lengths, normalized
intrinsics, proper rotation matrices, and image sizes. It cannot establish real
calibration accuracy. Synthetic tests do not establish physical measurement accuracy.
