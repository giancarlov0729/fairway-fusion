"""Render saved vision diagnostics without changing or reprocessing images."""

import cv2
import numpy as np


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
