"""미리보기 프레임에서 얼굴만 남기고 배경을 흐리게 만듭니다.

키오스크는 공공장소에 있습니다. 미리보기에 뒤에 선 사람이나 매장 내부가 그대로 찍히면
쓰는 사람도 지나가는 사람도 불편합니다. 얼굴 주변만 선명하게 두고 나머지는 흐립니다.

얼굴을 찾지 못하면 화면 전체를 흐립니다. 위치를 잡을 수 있을 만큼의 윤곽은 남지만
누구인지 알아보기는 어려운 상태가 됩니다.
"""
from __future__ import annotations

import cv2
import numpy as np


def landmark_bounds(landmarks):
    """랜드마크에서 0~1 정규화 경계상자 (x0, y0, x1, y1)를 구합니다. 없으면 None."""
    if not landmarks:
        return None
    try:
        points = list(landmarks)
    except TypeError:
        return None          # 랜드마크가 아닌 값이 들어오면 배경 전체를 흐립니다.
    xs, ys = [], []
    for point in points:
        x = getattr(point, "x", None)
        y = getattr(point, "y", None)
        if x is None or y is None:
            try:
                x, y = point[0], point[1]
            except (TypeError, IndexError, KeyError):
                continue
        if x is None or y is None:
            continue
        x, y = float(x), float(y)
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def blur_background(frame, landmarks=None, strength=.06, margin=1.35, feather=.5):
    """얼굴 바깥을 흐린 새 프레임을 돌려줍니다. 원본은 바꾸지 않습니다.

    strength: 프레임 너비 대비 흐림 정도. margin: 얼굴 경계상자를 넓히는 배율.
    feather: 경계를 부드럽게 하는 정도(타원 반지름 대비).
    """
    if frame is None or frame.size == 0:
        return frame
    h, w = frame.shape[:2]
    sigma = max(1.0, w*strength)
    blurred = cv2.GaussianBlur(frame, (0, 0), sigma)

    bounds = landmark_bounds(landmarks)
    if bounds is None:
        return blurred                      # 얼굴을 못 찾으면 전체를 흐립니다.

    x0, y0, x1, y1 = bounds
    cx, cy = (x0+x1)/2*w, (y0+y1)/2*h
    rx, ry = max(1., (x1-x0)/2*w*margin), max(1., (y1-y0)/2*h*margin)
    if not all(np.isfinite(v) for v in (cx, cy, rx, ry)):
        return blurred

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.ellipse(mask, (int(round(cx)), int(round(cy))),
                (int(round(rx)), int(round(ry))), 0, 0, 360, 255, -1)
    edge = max(1.0, min(rx, ry)*feather)
    mask = cv2.GaussianBlur(mask, (0, 0), edge)
    alpha = (mask.astype(np.float32)/255.)[:, :, None]
    return (frame*alpha + blurred*(1-alpha)).astype(frame.dtype)
