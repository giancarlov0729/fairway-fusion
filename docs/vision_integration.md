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

## Chunk 2: saved synthetic inputs

`create_synthetic_capture(store, session_id, case="clean", flash_interval_us=2000)`
in `services/synthetic_capture_service.py` requires a matching active practice
session. Available cases are Giancarlo's existing clean, leftward, shallow, blur,
uneven-light and clutter fixtures. Flash interval is explicitly stored in integer
microseconds and defaults to the fixture demo's 2000 us, not the Pico's 1000 us.
Changing the interval reinterprets the same two positions at a different speed;
it does not change the generator's positions or simulate physical output timing.

The service creates a preparing capture with source `simulation`, saves a new
synthetic calibration version, and writes these files relative to the database's
folder (the existing default is `src/instance/`, not Flask's separate instance path):

```
captures/<capture_id>/camera_a.png
captures/<capture_id>/camera_b.png
captures/<capture_id>/manifest.json
```

The PNGs are lossless 640x480 uint8 monochrome images. SQLite stores the image
paths, session ID, calibration ID and timing; no image blobs, expected positions,
launch results, or carry estimates are saved in this chunk. The manifest repeats
the identity/calibration/timing linkage and records each PNG's SHA-256 digest.
Camera ordering is always A then B. Digests detect accidental changes/swaps;
they are not digital signatures or proof of physical provenance.

After a successful read-back check the capture becomes `completed`, meaning
synthetic input generation is complete. No Pico sequence ran and no accepted shot
exists yet. Chunk 3 will transition saved inputs through processing and save the
accepted/rejected outcome. The current schema has no separate images-ready state.
File-writing failures mark the attempt `failed`, retain its error and capture ID,
and raise `CaptureAssetError`. Partial files remain as debugging evidence. Files
and SQLite are not one atomic transaction: interruption can leave partial files;
normal Flask startup marks an unfinished preparing record interrupted.

`load_synthetic_capture(store, capture_id)` returns `SavedStereoInputs` containing
the image arrays, reconstructed `CameraCalibration` objects, capture/session/
calibration IDs, source, flash interval in seconds, and timing source. It does not
regenerate fixtures or run vision. Call the chunk-1 adapter with its images,
cameras, interval, source and timing source to analyze the saved pair.

The loader verifies the manifest/database linkage, fixed per-capture paths,
synthetic provenance, file digests, PNG format, dtype and calibrated dimensions.
Missing files, cleanup-deleted images, symlink redirects and mismatched metadata
fail explicitly. Copy the database and its `captures` folder together for replay.
Cleanup may retain the small manifest after deleting PNGs; the database's
`images_deleted_at` field prevents those inputs from being loaded as available.

These functions are service-level building blocks only. Flask actions, UI image
display, shot saving, and automatic processing are reserved for chunks 3–5.

## Chunk 3: processing saved captures

`services/capture_processing_service.py` exposes
`process_saved_capture(store, capture_id)`, returning the saved shot dictionary.
It currently accepts only `simulation` captures and uses the chunk-2 loader and
chunk-1 adapter. No USB commands, new images, automatic cleanup or UI actions run.

`ShotStore.claim_capture_processing` takes an SQLite write lock, checks the source
and original session, and commits status `processing` plus processing version
`stereo-vision-v1`. Another request sees the busy state and cannot process it
again. Increment this version when intentionally changing the processing behavior.
The original session must still be active for a new processing attempt. Session
ending and cleanup are blocked while processing is underway.

An accepted vision result saves speed, angles, reconstructed positions/timestamps,
carry assumptions/estimate, source, session ID and capture ID. A normal vision
rejection saves a rejected shot and reason with no invented launch measurements.
In both cases, detections, matched centres, reprojection errors and vision outcome
are recorded in the capture's `detections` JSON field. The image files and original
calibration/timing links are preserved. Capture `completed` means the processing
operation finished; inspect the linked shot's status for accepted versus rejected.

`save_shot(finalize_capture=True, detections=..., expected_capture=...)` validates
the metadata snapshot and writes the shot and capture completion in ONE database
transaction. If completion fails, the shot insert rolls back too. Changed timing,
calibration, source, session or image paths cannot silently receive stale results.
The existing unique capture index remains a second guard against duplicate shots.

An unexpected processing/asset/storage exception marks the attempt `failed`, stores
the exception type and message, and raises `CaptureProcessingError` with capture ID
and chained cause. It does not fabricate a rejected shot. Available diagnostics
are retained if analysis finished before storage failed. If storage itself becomes
unavailable, updating the failure record may also fail; an unfinished processing
record is recovered as interrupted on normal server startup.

An explicit retry of failed/interrupted work can claim it again after the input
problem is fixed. Busy, preparing, armed, capturing, cancelled and timed-out
captures cannot be claimed. After a shot has already been saved, repeated requests
return that same shot without reloading images or rerunning vision, including
after the session ends. Carry-model failure leaves a valid launch accepted with
an empty carry estimate and recorded carry error.

No schema migration is needed: this uses the existing capture/shot columns.
Chunk 4 wires this service into Flask. UI actions remain pending until chunk 5.

## Chunk 4: Flask JSON actions

`POST /api/captures/synthetic` requires a JSON object. `{}` uses the active session,
clean fixture and 2000-us interval. Optional fields are `session_id`, `case` and
`flash_interval_us`. An explicit session must match the active session. Unknown
fields, including source/calibration/image overrides, are refused. Generation is
synchronous and creates only synthetic inputs; no Pico is connected or triggered.

Success returns 201 with `capture`, `status_url`, `process_url` and a Location header
pointing to the existing capture-status endpoint. Each successful creation request
is a NEW attempt, not an idempotent retry. If a response is lost, inspect the capture
log before creating again. Chunk 5 will use single-use tokens for HTML forms.

`POST /api/captures/<capture_id>/process` accepts an empty body or `{}` and returns
200 with `capture`, `shot`, `saved: true` and `result_url`. It uses only the inputs
already associated with that record; session/source/image/timing overrides are
refused. Repeating processing returns the same saved shot without duplication.
Normal vision rejection also returns 200: the shot status and reason explain it.

Error behavior:
- 400: invalid ID, JSON object, option, session ID type, or unsupported fields.
- 415: a nonempty body with an unsupported content type.
- 404: unknown capture ID.
- 409: no active/matching session, closed session for unsaved work, busy capture,
  or an attempt to process a physical capture through this synthetic workflow.
- 422: missing/changed/unavailable saved images or calibration; the capture is
  marked failed and included in the response for repair/retry.
- 500: generation or unexpected processing failure. The failed capture is included
  when available, while the underlying exception is logged in the server terminal.

`web/vision_routes.py` registers these endpoints in `create_app`. The shared
`require_capture_session` helper keeps real arming and synthetic generation aligned.
Generation uses the shared session lock; processing uses SQLite's atomic claim, so
parallel processing/session-end requests receive conflicts instead of duplicates.
The existing physical Arm endpoints and mock-controller forms remain unchanged.

## Chunk 5: server-rendered vision demo

Open `/vision` or choose **Vision demo** in the sidebar. Start a practice session,
select a scenario, and submit **Generate & save images**. This uses the chunk-2
service at 2000 us and redirects to `/vision?capture_id=<id>`. Both saved original
PNGs are displayed; generating images still does not save a shot. Submit **Process
saved images** to invoke chunk 3, then inspect accepted measurements or the saved
rejection reason. **Open Shot Analysis** links to the existing results page.

All controls use ordinary HTML forms and existing single-use tokens. Reusing a
form submission cannot duplicate an attempt; refreshes and image requests do not
run processing. A repaired failed capture can be retried while its original session
is active. The page preserves the selected ID in its URL and lists recent synthetic
image attempts separately from mock timing-controller captures. A completed input
generation is labelled **Images ready**, not as a processed or measured shot.

`GET /vision/captures/<id>/images/a` and `/images/b` load and verify the saved pair
through chunk 2, then return PNG image bytes. They do not expose arbitrary disk
paths, serve physical captures, or substitute missing images with illustrations.
Missing, changed, cleanup-deleted or invalid inputs show an unavailable message.

After processing, expand **Inspect detection & matching overlay**. The overlay
endpoint `/vision/captures/<id>/overlay` draws persisted detections/matches and the
saved outcome over the original pair; it does not rerun vision. The drawing code
was moved unchanged from `scripts/analyze_shot.py` to `services/vision_overlay.py`,
which the original CLI still imports. Overlays are generated on request and not
stored as replacement raw images. An unprocessed capture has no overlay.

The synthetic banner, pixel-source labels and no-hardware notice distinguish this
screen from mock Pico controls and future physical captures. No JavaScript, file
uploads, camera acquisition, or physical Arm controls are added in this chunk.

## Chunk 6: failure and replay verification

Run the complete suite with `python -m pytest tests -q`. In addition to the earlier
unit and UI checks, recovery tests now copy the SQLite database and captures folder
to another directory and process each fixture from saved files. Fixture generation
is explicitly blocked during replay, and the original database remains unchanged.
Tests cover Flask startup during preparing/processing, retry after interruption,
media unavailability after cleanup while saved results remain readable, real-
calibration substitution, and a processing-version change during analysis.

For an inspectable smoke test, run `python scripts/verify_vision_workflow.py`.
It creates a fresh isolated directory, exercises all six fixtures through Flask's
test client, checks accepted measurements against known synthetic positions, checks
rejections and duplicate processing, and validates PNG/overlay/result routes. It
then makes an SQLite backup with a copy of the captures folder and analyzes a copied
unprocessed pair. The JSON report records each outcome and synthetic error; these
numbers are NOT measurements of real-world system accuracy.

The script leaves `original/`, `replay/` and `verification_report.json` for review.
Use `--output-dir <new-folder>` to choose a destination. Existing folders are never
overwritten. No port is opened, hardware is not accessed, and the normal application
database is not modified. Retain both the database and captures folder for replay.

Runtime `src/instance/` and `instance/` folders are ignored by Git so generated PNGs,
manifests, and databases are not accidentally committed. No existing data is deleted.
The README now includes the image workflow, full dependency installation and the
verification command. Real camera acquisition, physical calibration and scope/
photodiode timing measurements remain separate hardware validation work.
