"""Calibration and quality gates. Coordinates are WKWebView CSS pixels."""
import json
import math
import os
from pathlib import Path

import numpy as np

CAL_POINTS = [(x, y) for y in (.12, .50, .88) for x in (.10, .50, .90)]
CHECK_POINTS = [(.23, .23), (.77, .76), (.23, .76)]


def design(v):
    x, y = v
    return np.array([1., x, y, x*x, y*y, x*y])


def solve(samples, targets, ridge):
    s, t = np.asarray(samples), np.asarray(targets)
    mean, std = s.mean(0), np.maximum(s.std(0), 1e-4)
    a = np.stack([design(v) for v in (s-mean)/std])
    penalty = np.eye(6) * ridge
    penalty[0, 0] = 1e-8
    weights = np.linalg.solve(a.T@a + penalty, a.T@t)
    return {"mean": mean.tolist(), "std": std.tolist(), "weights": weights.tolist(), "ridge": ridge}


def predict(profile, iris):
    return design((np.asarray(iris)-profile['mean'])/profile['std']) @ np.asarray(profile['weights'])


def fit(samples, targets):
    s, t = np.asarray(samples), np.asarray(targets)
    if len(s) != len(CAL_POINTS) or not np.isfinite(s).all() or not np.isfinite(t).all():
        raise ValueError('보정 표본이 부족합니다. 처음부터 다시 진행해 주세요.')
    if np.ptp(s[:, 0]) < .012 or np.ptp(s[:, 1]) < .006:
        raise ValueError('눈동자 움직임이 충분히 측정되지 않았습니다. 화면의 점을 바라봐 주세요.')
    scores = []
    for ridge in (.001, .01, .03, .1, .3, 1., 3.):
        errors = []
        for i in range(len(s)):
            mask = np.arange(len(s)) != i
            q = solve(s[mask], t[mask], ridge)
            errors.append(float(np.linalg.norm(predict(q, s[i])-t[i])))
        scores.append((float(np.mean(errors)), ridge))
    loo, ridge = min(scores)
    result = solve(s, t, ridge)
    result['loo_mean_px'] = loo
    return result


def fit_gaze_zones(samples, spreads=None):
    """Fit 3x3 gaze zones only when calibration separation exceeds measured jitter."""
    s = np.asarray(samples, dtype=float)
    if s.shape != (len(CAL_POINTS), 2) or not np.isfinite(s).all():
        return None
    if spreads is None:
        noise = np.zeros(2)
    else:
        spread_array = np.asarray(spreads, dtype=float)
        if spread_array.shape != (len(CAL_POINTS), 2) or not np.isfinite(spread_array).all() or np.any(spread_array < 0):
            return None
        noise = spread_array.max(axis=0)
    cols = [float(np.median(s[np.arange(3)*3+j, 0])) for j in range(3)]
    rows = [float(np.median(s[i*3:i*3+3, 1])) for i in range(3)]

    def axis(values, axis_noise):
        delta = np.diff(values)
        if min(abs(float(v)) for v in delta) < max(.004, float(axis_noise)*1.8) or delta[0]*delta[1] <= 0:
            return None
        return dict(cuts=[(values[0]+values[1])/2, (values[1]+values[2])/2],
                    ascending=bool(delta[0] > 0))

    x, y = axis(cols, noise[0]), axis(rows, noise[1])
    return None if x is None or y is None else dict(x=x, y=y)


def classify_gaze_zone(zones, iris):
    """Return row-major 0..8 zone, or None when the model is unavailable."""
    try:
        def bin_axis(value, axis):
            a, b = axis['cuts']
            if axis['ascending']:
                return 0 if value < a else (1 if value < b else 2)
            return 2 if value < b else (1 if value < a else 0)
        return bin_axis(float(iris[1]), zones['y'])*3 + bin_axis(float(iris[0]), zones['x'])
    except (TypeError, KeyError, ValueError, IndexError):
        return None


def validate(profile, samples, targets, size):
    if len(samples) != len(CHECK_POINTS):
        raise ValueError('확인용 세 점이 모두 필요합니다.')
    scale = np.asarray(size, dtype=float)
    errors = [float(np.linalg.norm((predict(profile, x)-y)*scale)) for x, y in zip(samples, targets)]
    mean, p95 = float(np.mean(errors)), float(np.percentile(errors, 95))
    diag = math.hypot(*size)
    mean_limit, p95_limit = min(90., diag*.06), min(150., diag*.11)
    return dict(mean_px=mean, p95_px=p95, errors_px=errors,
                mean_limit_px=mean_limit, p95_limit_px=p95_limit,
                passed=bool(mean <= mean_limit and p95 <= p95_limit))


def head_vector(features, width):
    return np.array([features[k] for k in ('head_x', 'head_y', 'pos_x', 'pos_y')] + [features['face_w']/width])


def head_ok(head, reference):
    delta = np.abs(np.asarray(head)-np.asarray(reference))
    return bool(np.all(delta[:4] < [.055, .045, .075, .075]) and delta[4] < max(.025, reference[4]*.22))


def stable_summary(samples):
    s = np.asarray(samples)
    center = np.median(s, axis=0)
    mad = np.maximum(np.median(np.abs(s-center), axis=0), .001)
    good = s[np.all(np.abs(s-center) < 4.5*mad, axis=1)]
    if len(good) < 10 or np.any(np.std(good, axis=0) > [.026, .020]):
        raise ValueError('시선이 흔들렸습니다. 점을 계속 바라봐 주세요.')
    return np.median(good, axis=0).tolist(), np.std(good, axis=0).tolist()


def stable_median(samples):
    return stable_summary(samples)[0]


def save_profile(path, profile):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2, allow_nan=False))
    os.replace(tmp, path)


def load_profile(path, camera_id, geometry):
    try:
        p = json.loads(Path(path).read_text())
        if p.get('schema') != 4 or p.get('camera_id') != camera_id:
            return None
        if p.get('geometry') != {'coordinate_space': 'normalized'}:
            return None
        if not (p.get('validation', {}).get('passed') or p.get('validation', {}).get('pointer_usable') is True):
            return None
        if np.shape(p['weights']) != (6, 2) or np.shape(p['mean']) != (2,) or np.shape(p['std']) != (2,):
            return None
        if not all(np.isfinite(p[k]).all() for k in ('weights','mean','std','head_reference')) or np.any(np.asarray(p['std']) <= 0):
            return None
        return p
    except (OSError, ValueError, TypeError, KeyError):
        return None
