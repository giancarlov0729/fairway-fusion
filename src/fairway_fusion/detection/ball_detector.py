from functools import lru_cache

import cv2
import numpy as np


_ANGLES = np.linspace(0, 2 * np.pi, 96, endpoint=False)
_RING_X = np.cos(_ANGLES)
_RING_Y = np.sin(_ANGLES)
_MIRROR_X = (len(_ANGLES) // 2 - np.arange(len(_ANGLES))) % len(_ANGLES)
_TOP_ARC = _RING_Y < -0.25
_BOTTOM_ARC = _RING_Y > 0.25


def _moving_ball(blurred, change, min_radius, max_radius, *, threshold=18):
    """Find a filled circle that has just appeared at a new position."""
    _, mask = cv2.threshold(change, threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = blurred.shape
    best_score = 0
    best = None
    for contour in contours:
        (x, y), radius = cv2.minEnclosingCircle(contour)
        if not min_radius <= radius <= max_radius:
            continue
        if min(x - radius, y - radius, width - 1 - x - radius, height - 1 - y - radius) < 0:
            continue
        occupancy = cv2.contourArea(contour) / (np.pi * radius**2)
        if occupancy < 0.62:
            continue

        def ring(scale, image):
            xx = np.clip(np.rint(x + radius * scale * _RING_X).astype(int), 0, width - 1)
            yy = np.clip(np.rint(y + radius * scale * _RING_Y).astype(int), 0, height - 1)
            return image[yy, xx].astype(np.int16)

        # Change alone also finds moving hands and lighting. Require evidence of
        # a brighter, filled disk on the current frame before accepting it.
        outside = ring(1.12, blurred)
        support = float(np.mean(ring(0.82, blurred) - outside >= 10))
        if support < 0.50:
            continue
        core = np.concatenate([ring(scale, blurred) for scale in (0.2, 0.4, 0.6)])
        if np.mean(core > np.percentile(outside, 35) + 15) < 0.70:
            continue
        contrast = np.median(ring(0.75, change))
        score = contrast * support * occupancy
        if contrast >= 25 and score > best_score:
            best_score = score
            fitted_radius = max(min_radius, radius * np.sqrt(occupancy))
            best = {"center": (round(x), round(y)), "radius": round(fitted_radius),
                    "circularity": support}
    return best


def detect_moving_ball(image, previous, min_radius, max_radius, *, mixed_contrast=False):
    """Check consecutive monochrome views for a moving ball without a circle scan."""
    blurred = cv2.GaussianBlur(image, (5, 5), 0)
    background = cv2.GaussianBlur(previous, (5, 5), 0)
    if mixed_contrast:
        # A white ball can darken one part of a bright background while
        # brightening another. The current-frame rim and core still gate it.
        return _moving_ball(blurred, cv2.absdiff(blurred, background), min_radius,
                            max_radius, threshold=12)
    return _moving_ball(blurred, cv2.subtract(blurred, background), min_radius, max_radius)


@lru_cache(maxsize=64)
def _ball_template(radius):
    half = int(np.ceil(1.25 * radius / 2)) * 2
    coords = np.arange(-half, half + 1, 2)
    distance = np.hypot(coords[:, None], coords)
    # The annulus supplies local contrast without encoding the scene behind it.
    template = np.zeros(distance.shape, dtype=np.float32)
    template[distance <= 0.88 * radius] = 1
    template[(distance >= radius) & (distance <= 1.25 * radius)] = -1
    return template, half


def follow_ball(image, prior, min_radius, max_radius):
    """Reacquire a known ball close to its previous center in the current image."""
    radius = prior["radius"]
    radii = dict.fromkeys(round(radius * scale) for scale in (1, 0.85, 1.16, 1.33))
    best = None
    best_score = 0
    for candidate_radius in radii:
        if min_radius <= candidate_radius <= max_radius:
            score, candidate = _match_ball_radius(image, prior, candidate_radius)
            if candidate is not None and score > best_score:
                best_score, best = score, candidate
                if candidate_radius == radius and score >= 0.98:
                    break
    return best


def _match_ball_radius(image, prior, radius):
    x, y = prior["center"]
    template, half = _ball_template(radius)
    height, width = image.shape
    reach = max(25, 3 * prior["radius"])
    left, right = max(0, x - reach - half), min(width, x + reach + half + 1)
    top, bottom = max(0, y - reach - half), min(height, y + reach + half + 1)
    if right - left <= 2 * half or bottom - top <= 2 * half:
        return 0, None
    blurred = cv2.GaussianBlur(image[top:bottom, left:right], (5, 5), 0)
    # Search at half resolution, then check the candidate's rim on the native image.
    search = blurred[::2, ::2].astype(np.float32)
    scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    dx = (2 * np.arange(scores.shape[1]) + left + half - x) / reach
    dy = (2 * np.arange(scores.shape[0]) + top + half - y) / reach
    scores -= 0.12 * (dx[None, :]**2 + dy[:, None]**2)
    _, score, _, (peak_x, peak_y) = cv2.minMaxLoc(scores)
    if score < 0.34:
        return 0, None

    cx, cy = 2 * peak_x + half, 2 * peak_y + half

    def ring(scale):
        xx = np.rint(cx + radius * scale * _RING_X).astype(int)
        yy = np.rint(cy + radius * scale * _RING_Y).astype(int)
        return blurred[yy, xx].astype(np.int16)

    outside = ring(1.12)
    support = float(np.mean(ring(0.82) - outside >= 9))
    core = np.concatenate([ring(scale) for scale in (0.2, 0.4, 0.6)])
    if support < 0.43 or np.mean(core > np.percentile(outside, 35) + 12) < 0.65:
        return 0, None
    # Prefer shape and rim evidence over a small change in apparent size.
    score += 0.20 * support - 0.12 * abs(radius - prior["radius"]) / prior["radius"]
    return score, {"center": (left + cx, top + cy), "radius": radius, "circularity": support}


def detect_ball(frame, min_radius=5, max_radius=200, *, previous=None, prior=None,
                prefer_roundness=False):
    """Return the best supported bright, filled circle in the radius range.

    A recent frame from the same camera favors candidates that have moved.
    An optional prior (x, y, radius) is checked against the current image
    like any other proposal, so a weak rim can still support a detection.
    """
    if frame is None:
        return None
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    brighter = None
    if previous is not None:
        previous_gray = previous if previous.ndim == 2 else cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
        # Positive change ignores the old location when a bright ball moves away.
        brighter = cv2.subtract(blurred, cv2.GaussianBlur(previous_gray, (5, 5), 0))
        moving = _moving_ball(blurred, brighter, min_radius, max_radius)
        if moving is not None:
            return moving
    candidates = _circle_candidates(gray, blurred, min_radius, max_radius, prior,
                                    prefer_roundness, brighter)
    return candidates[0] if candidates else None


def detect_ball_candidates(frame, min_radius, max_radius, *, prefer_roundness=True):
    """Return distinct, shape-checked candidates without making a lock decision."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    return _circle_candidates(gray, blurred, min_radius, max_radius, None, prefer_roundness, None)


def _circle_candidates(gray, blurred, min_radius, max_radius, prior, prefer_roundness, brighter):
    candidates = [prior] if prior is not None else []
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        if perimeter and 4 * np.pi * cv2.contourArea(contour) / perimeter**2 >= 0.75:
            (x, y), radius = cv2.minEnclosingCircle(contour)
            candidates.append((x, y, radius))
    # Circle proposals recover rims that merge with the tabletop in the thresholded
    # image. Use both gradient modes because weak or blurred rims can be incomplete.
    for method, dp, edge_threshold, circle_threshold in (
        (cv2.HOUGH_GRADIENT, 1, 60, 16),
        (cv2.HOUGH_GRADIENT_ALT, 1.5, 90, 0.65),
    ):
        circles = cv2.HoughCircles(
            blurred, method, dp=dp, minDist=max(8, min_radius // 2),
            param1=edge_threshold, param2=circle_threshold,
            minRadius=int(min_radius), maxRadius=int(max_radius),
        )
        if circles is not None:
            candidates.extend(circles[0])
    if not candidates:
        return []

    centers = np.asarray(candidates, dtype=np.float32)
    x, y, radius = centers.T
    height, width = gray.shape
    in_bounds = ((radius >= min_radius) & (radius <= max_radius)
                 & (x - radius >= 0) & (x + radius < width)
                 & (y - radius >= 0) & (y + radius < height))
    x, y, radius = centers[in_bounds].T
    if not radius.size:
        return []

    def ring(scale, image=blurred):
        distance = radius[:, np.newaxis] * scale
        xx = np.clip(np.rint(x[:, np.newaxis] + _RING_X * distance).astype(int), 0, width - 1)
        yy = np.clip(np.rint(y[:, np.newaxis] + _RING_Y * distance).astype(int), 0, height - 1)
        return image[yy, xx].astype(np.int16)

    inside, outside = ring(0.82), ring(1.12)
    support = np.mean(inside - outside >= 10, axis=1)
    sharpness = np.mean(ring(0.93) - ring(1.07) >= 8, axis=1)
    # The bottom arc can blend into the surface. The core still needs to be
    # bright and filled, which rules out bright rings and hollow logos.
    core = np.concatenate([ring(scale) for scale in (0.2, 0.4, 0.6)], axis=1)
    background = np.percentile(outside, 35, axis=1)
    fill = np.mean(core > background[:, np.newaxis] + 15, axis=1)
    valid = (support >= 0.58) & (fill >= 0.70)

    if prefer_roundness:
        # A monitor or reflection can make a large bright proposal. Reward
        # edges that stay near the same radius around the candidate and a
        # bright core even in its dimmer sectors.
        edges = np.stack([ring(scale - .04) - ring(scale + .04)
                          for scale in np.linspace(.82, 1.18, 11)], axis=1)
        peak = np.argmax(edges, axis=1)
        aligned = np.mean((abs(peak - 5) <= 2) & (np.max(edges, axis=1) > 12), axis=1)
        # Hough can fit an arc across unrelated edges in clutter. For a new
        # stationary lock, require a rim at a consistent radius around the center.
        valid &= aligned >= 0.60
        # Overhead light can make the lower half of a ball darker than the
        # turf. Accept either edge polarity when the rim stays circular and
        # the interior is shaded from top to bottom with similar left and right sides.
        magnitude = abs(edges)
        strongest = np.max(magnitude, axis=1)
        unsigned_rim = np.mean((abs(np.argmax(magnitude, axis=1) - 5) <= 2)
                               & (strongest > 10), axis=1)
        near_rim = np.mean(np.max(magnitude[:, 3:8], axis=1) >= 0.8 * strongest, axis=1)
        middle = ring(0.65)
        vertical_shading = np.mean(middle[:, _TOP_ARC], axis=1) - np.mean(middle[:, _BOTTOM_ARC], axis=1)
        asymmetry = np.mean(abs(middle - middle[:, _MIRROR_X]), axis=1)
        shaded_ball = ((support >= 0.45) & (fill >= 0.70) & (unsigned_rim >= 0.51)
                       & (near_rim >= 0.76) & (vertical_shading >= 32) & (asymmetry <= 20))
        valid |= shaded_ball
        dim_core = np.percentile(core, 20, axis=1) / 255
        tight_edge = np.mean(np.clip(ring(.94) - ring(1.06), 0, 55), axis=1) / 55
        score = (2 * support + aligned + 0.8 * dim_core + 0.55 * tight_edge
                 + 0.25 * fill + 0.6 * radius / max_radius - np.std(core, axis=1) / 300)
        score = np.where(shaded_ball, np.maximum(score, 3 + unsigned_rim + near_rim), score)
        # When silhouettes are otherwise comparable, the near turf is usually
        # lower in a view aimed across the hitting surface.
        score += 0.65 * y / height
    else:
        score = (support + 0.25 * sharpness + 0.25 * fill) * radius**0.20
    if not valid.any():
        return []
    if brighter is not None:
        changed = np.mean(ring(0.82, brighter) >= 15, axis=1)
        score *= 1 + changed
    distinct = []
    indices = np.flatnonzero(valid)
    for index in indices[np.argsort(-score[indices], kind="stable")]:
        center = (int(round(x[index])), int(round(y[index])))
        r = int(round(radius[index]))
        # Contours and both Hough passes often describe the same rim.
        if any(np.hypot(center[0] - ball["center"][0], center[1] - ball["center"][1])
               <= 0.55 * min(r, ball["radius"]) for ball in distinct):
            continue
        distinct.append({"center": center, "radius": r, "circularity": float(support[index])})
    return distinct


def detect_ball_centers(image, *, threshold=100, min_radius=5, max_radius=200):
    """Find isolated bright ball silhouettes in a uint8 monochrome image."""
    if image.ndim != 2 or image.dtype != np.uint8:
        raise ValueError("Expected an 8-bit monochrome image")

    _, mask = cv2.threshold(image, threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = image.shape
    centers = []
    for contour in contours:
        (x, y), radius = cv2.minEnclosingCircle(contour)
        if not min_radius <= radius <= max_radius:
            continue
        if min(x - radius, y - radius, width - 1 - x - radius, height - 1 - y - radius) <= 0:
            continue

        area = cv2.contourArea(contour)
        # Reject merged or irregular silhouettes instead of splitting them.
        if area >= 0.82 * np.pi * radius**2:
            centers.append((x, y))
    return centers
