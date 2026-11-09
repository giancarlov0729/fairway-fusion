"""Run the stereo prototype with a synthetic shot on a laptop."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from fairway_fusion.pipeline import analyze_stereo_shot
from fairway_fusion.synthetic import CASE_POSITIONS_M, make_stereo_fixture


def render_overlay(images, cameras, diagnostics, result):
    """Mark detected centers and accepted flash matches without changing the inputs."""
    colors = [(90, 230, 80), (255, 205, 65)]
    panels = []
    for view, (image, camera) in enumerate(zip(images, cameras)):
        width, height = camera.image_size
        panel = np.full((height + 36, width, 3), 30, dtype=np.uint8)
        title = f"Camera {'AB'[view]}"
        if image is None:
            title += "  |  Missing frame"
        else:
            panel[36:] = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        for x, y in diagnostics["detected_centers_px"][view]:
            cv2.drawMarker(
                panel, (round(x), round(y) + 36), (70, 190, 255),
                cv2.MARKER_CROSS, 17, 2, cv2.LINE_AA,
            )
        for index, match in enumerate(diagnostics["matched_centers_px"]):
            x, y = match[view]
            center = (round(x), round(y) + 36)
            color = colors[index]
            cv2.circle(panel, center, 19, color, 2, cv2.LINE_AA)
            cv2.putText(panel, str(index + 1), (center[0] + 21, center[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

        cv2.putText(panel, title, (12, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (245, 245, 245), 1, cv2.LINE_AA)
        panels.append(panel)

    composite = np.hstack(panels)
    footer = np.full((40, composite.shape[1], 3), 30, dtype=np.uint8)
    message = result.get("reason", "Matched centers share the same flash number in both views")
    cv2.putText(footer, f"{result['status'].upper()}  |  {message}", (12, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.57, (245, 245, 245), 1, cv2.LINE_AA)
    return np.vstack((composite, footer))


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
