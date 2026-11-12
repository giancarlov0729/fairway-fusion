"""Run the stereo prototype with a synthetic shot on a laptop."""

import argparse
import json
from pathlib import Path

import cv2

from fairway_fusion.services.vision_overlay import render_overlay
from fairway_fusion.pipeline import analyze_stereo_shot
from fairway_fusion.synthetic import CASE_POSITIONS_M, make_stereo_fixture



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASE_POSITIONS_M, default="clean")
    parser.add_argument("--missing-frame", action="store_true", help="Show a rejected shot")
    parser.add_argument("--overlay", type=Path, metavar="PNG", help="Save annotated camera views")
    args = parser.parse_args()

    images, cameras, positions_m = make_stereo_fixture(case=args.case)
    if args.missing_frame:
        images[1] = None
    diagnostics = {} if args.overlay else None
    result = analyze_stereo_shot(
        *images, *cameras, flash_interval_s=0.002, diagnostics=diagnostics
    )
    if args.overlay:
        if args.overlay.suffix.lower() != ".png":
            parser.error("--overlay requires a .png path")
        try:
            saved = cv2.imwrite(str(args.overlay), render_overlay(images, cameras, diagnostics, result))
        except cv2.error:
            saved = False
        if not saved:
            parser.error(f"Could not write overlay to {args.overlay}")
    print(json.dumps({
        "source": "synthetic",
        "expected_positions_m": positions_m.tolist(),
        "result": result,
    }, indent=2))


if __name__ == "__main__":
    main()
