#!/usr/bin/env python3
"""SeeOSK 카메라 마우스 — 웹캠 종류와 무관하게 동작하는 머리/시선 포인팅 + 고신뢰 클릭.

설계 원칙
  1) 모든 특징량은 얼굴 크기로 나눈 비율이라 카메라 화각·해상도·거리에 둔감하다.
  2) 카메라별 캘리브레이션 프로파일을 따로 저장해 장비를 바꿔도 재학습 없이 전환한다.
  3) 포인팅 신호와 클릭 신호를 분리하고, 클릭은 '조준 시점 좌표'에 찍는다.
"""

import argparse
import json
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "face_landmarker.task"
PROFILE_DIR = ROOT / "profiles"

# ---------------------------------------------------------------- 카메라 탐색

def avf_devices():
    """AVFoundation 장치를 순서대로 읽는다. 카메라를 켜지 않으므로 아이폰이 깨어나지 않는다.

    OpenCV의 AVFoundation 백엔드도 같은 순서의 인덱스를 쓴다.
    """
    try:
        import AVFoundation as AV
    except ImportError:
        return []
    names = ("AVCaptureDeviceTypeBuiltInWideAngleCamera", "AVCaptureDeviceTypeExternal",
             "AVCaptureDeviceTypeContinuityCamera", "AVCaptureDeviceTypeDeskViewCamera")
    types = [getattr(AV, n) for n in names if hasattr(AV, n)]
    session = AV.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
        types, AV.AVMediaTypeVideo, AV.AVCaptureDevicePositionUnspecified)
    out = []
    for i, d in enumerate(session.devices()):
        kind = str(d.deviceType()).replace("AVCaptureDeviceType", "")
        out.append({"index": i, "name": str(d.localizedName()), "kind": kind,
                    "builtin": kind == "BuiltInWideAngleCamera",
                    "phone": kind in ("ContinuityCamera", "DeskViewCamera")})
    return out


def list_cameras():
    devs = avf_devices()
    if not devs:
        print("AVFoundation 장치 목록을 읽지 못했습니다.")
        return
    for d in devs:
        tag = " (내장)" if d["builtin"] else (" (아이폰 연속성)" if d["phone"] else " (외장)")
        print(f"  [{d['index']}] {d['name']}{tag}")


def resolve_camera(spec):
    """인덱스 또는 이름을 (인덱스, 이름)으로 바꾼다. 장치를 열어보지 않는다."""
    devs = avf_devices()
    if spec is not None:
        try:
            idx = int(spec)
        except ValueError:
            for d in devs:
                if spec.lower() in d["name"].lower():
                    return d["index"], d["name"]
            raise SystemExit(f"'{spec}' 이름의 카메라가 없습니다. --list 로 확인하세요.")
        for d in devs:
            if d["index"] == idx:
                return idx, d["name"]
        return idx, f"카메라 {idx}"

    for d in devs:                      # 기본값은 내장 카메라. 아이폰을 깨우지 않는다.
        if d["builtin"]:
            return d["index"], d["name"]
    for d in devs:
        if not d["phone"]:
            return d["index"], d["name"]
    raise SystemExit("사용할 카메라를 찾지 못했습니다. --list 로 확인하고 --camera 로 지정하세요.")


def safe_name(text):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:60]


# ---------------------------------------------------------------- 신호 처리

class OneEuro:
    """1€ 필터. 정지 중에는 떨림을 죽이고 빠르게 움직일 때는 지연을 줄인다."""

    def __init__(self, mincutoff=1.2, beta=0.02, dcutoff=1.0):
        self.mincutoff, self.beta, self.dcutoff = mincutoff, beta, dcutoff
        self.x_prev = None
        self.dx_prev = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * np.pi * cutoff)
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def __call__(self, x, dt):
        if self.x_prev is None:
            self.x_prev = x
            return x
        dx = (x - self.x_prev) / max(dt, 1e-6)
        a_d = self._alpha(self.dcutoff, dt)
        self.dx_prev = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.mincutoff + self.beta * abs(self.dx_prev)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev = x_hat
        return x_hat


# ---------------------------------------------------------------- 특징 추출

L_EYE_OUT, L_EYE_IN = 33, 133
R_EYE_IN, R_EYE_OUT = 362, 263
NOSE_TIP = 1
L_IRIS, R_IRIS = 468, 473


def extract_features(landmarks, w, h):
    """카메라에 무관한 비율 특징을 만든다.

    얼굴 좌우 눈초리를 잇는 축을 기준으로 삼아 고개 기울임(roll)을 상쇄하고,
    눈 사이 거리로 나눠 해상도·화각·거리 차이를 없앤다.
    """
    def px(i):
        lm = landmarks[i]
        return np.array([lm.x * w, lm.y * h], dtype=np.float64)

    eye_l, eye_r = px(L_EYE_OUT), px(R_EYE_OUT)
    axis = eye_r - eye_l
    face_w = float(np.linalg.norm(axis))
    if face_w < 1e-3:
        return None
    e = axis / face_w                    # 눈 축 방향(좌→우)
    p = np.array([-e[1], e[0]])          # 그에 수직인 축(위→아래)
    eye_mid = (eye_l + eye_r) / 2.0

    nose = px(NOSE_TIP) - eye_mid
    head_x = float(np.dot(nose, e)) / face_w
    head_y = float(np.dot(nose, p)) / face_w

    feats = {"head_x": head_x, "head_y": head_y, "face_w": face_w, "has_iris": False,
             "pos_x": float(eye_mid[0]) / w, "pos_y": float(eye_mid[1]) / h}

    if len(landmarks) > R_IRIS:
        gx, gy = [], []
        for iris, outer, inner in ((L_IRIS, L_EYE_OUT, L_EYE_IN), (R_IRIS, R_EYE_OUT, R_EYE_IN)):
            c_out, c_in = px(outer), px(inner)
            eye_c = (c_out + c_in) / 2.0
            eye_w = float(np.linalg.norm(c_out - c_in))
            if eye_w < 1e-3:
                continue
            d = px(iris) - eye_c
            gx.append(float(np.dot(d, e)) / eye_w)
            gy.append(float(np.dot(d, p)) / eye_w)
        if gx:
            feats["gaze_x"] = float(np.mean(gx))
            feats["gaze_y"] = float(np.mean(gy))
            feats["has_iris"] = True
    return feats


RAW_KEYS = ("head_x", "head_y", "pos_x", "pos_y", "gaze_x", "gaze_y")

# 포인팅 모드 = 원시 특징 중 어떤 것을 쓰느냐의 차이일 뿐이다.
# 캘리브레이션 한 번으로 모든 모드를 같은 데이터에서 비교할 수 있다.
MODES = {
    "head":   (0, 1),              # 머리 자세만
    "headpos": (0, 1, 2, 3),       # 머리 자세 + 프레임 내 위치
    "iris":   (4, 5),              # 눈동자만
    "gaze":   (4, 5, 0, 1),        # 눈동자 + 머리 자세 보정
    "all":    (0, 1, 2, 3, 4, 5),
}


def raw_vector(feats):
    return np.array([feats.get(k, 0.0) for k in RAW_KEYS])


def feature_vector(feats, mode):
    return raw_vector(feats)[list(MODES[mode])]


def select(raw, mode):
    return np.asarray(raw)[..., list(MODES[mode])]


def design_row(v):
    """선형 + 제곱 + 주 교차항. 항 수가 캘리브레이션 점 수를 넘지 않게 억제한다.

    특징 4개 기준 10항, 6개 기준 14항. 점 16개면 둘 다 여유가 있다.
    """
    terms = [1.0]
    terms.extend(v.tolist())
    terms.extend((v * v).tolist())
    terms.append(v[0] * v[1])
    return np.array(terms)


RIDGE_GRID = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 3e-2, 1e-1, 3e-1, 1.0, 3.0, 10.0)


def _solve(S, B, ridge):
    mean = S.mean(axis=0)
    std = np.maximum(S.std(axis=0), 1e-4)
    A = np.stack([design_row(z) for z in (S - mean) / std])
    W = np.linalg.solve(A.T @ A + ridge * np.eye(A.shape[1]), A.T @ B)
    return W, mean, std, A


def loo_error(S, B, ridge):
    """Leave-one-out 오차. 훈련 잔차와 달리 실제 사용 정확도에 가깝다."""
    errs = []
    for i in range(len(S)):
        m = np.ones(len(S), bool); m[i] = False
        W, mean, std, _ = _solve(S[m], B[m], ridge)
        pred = design_row((S[i] - mean) / std) @ W
        errs.append(float(np.linalg.norm(pred - B[i])))
    return np.array(errs)


def fit_mapping(samples, targets, ridge=None):
    """표준화 + 2차 다항 회귀. 릿지 계수는 LOO 오차가 최소인 값으로 고른다."""
    S = np.stack(samples).astype(np.float64)
    B = np.asarray(targets, dtype=np.float64)
    if ridge is None:
        scored = [(loo_error(S, B, r).mean(), r) for r in RIDGE_GRID]
        ridge = min(scored)[1]
    loo = loo_error(S, B, ridge)
    W, mean, std, A = _solve(S, B, ridge)
    err = np.linalg.norm(A @ W - B, axis=1)
    return {
        "weights": W, "mean": mean, "std": std, "ridge": float(ridge),
        "mean_error_px": float(np.mean(err)),
        "p95_error_px": float(np.percentile(err, 95)),
        "loo_mean_px": float(loo.mean()),
        "loo_p95_px": float(np.percentile(loo, 95)),
        "feature_spread": (S.max(axis=0) - S.min(axis=0)).tolist(),
    }


def compare_modes(raw_samples, targets):
    """같은 캘리브레이션 데이터로 모든 포인팅 방식을 LOO 비교한다."""
    rows = []
    for name in MODES:
        S = select(np.stack(raw_samples), name)
        f = fit_mapping(list(S), targets)
        rows.append((name, f["loo_mean_px"], f["loo_p95_px"], f["mean_error_px"], f["ridge"]))
    return sorted(rows, key=lambda r: r[1])


def apply_mapping(prof, v):
    z = (v - prof["mean"]) / prof["std"]
    return design_row(z) @ prof["weights"]


# ---------------------------------------------------------------- 클릭 판정

BLEND_GESTURES = {
    "jawopen":   (["jawOpen"], 0.30),
    "browup":    (["browInnerUp", "browOuterUpLeft", "browOuterUpRight"], 0.30),
    "smile":     (["mouthSmileLeft", "mouthSmileRight"], 0.30),
    "pucker":    (["mouthPucker"], 0.35),
    "cheekpuff": (["cheekPuff"], 0.25),
    "winkleft":  (["eyeBlinkLeft"], 0.45),
    "winkright": (["eyeBlinkRight"], 0.45),
    "longblink": (["eyeBlinkLeft", "eyeBlinkRight"], 0.45),
}
ALL_GESTURES = list(BLEND_GESTURES) + ["nod", "dwell", "none"]


class ThresholdDetector:
    """표정 기반 클릭. 자동 기준선 + 이중 임계값 + 연속 프레임 + 불응기."""

    def __init__(self, name, k_on=6.0, min_margin=None, hold=0.12, refractory=0.9,
                 baseline_sec=3.0, manual_on=None):
        keys, default_margin = BLEND_GESTURES[name]
        self.name = name
        self.keys = keys
        self.exclusive = name in ("winkleft", "winkright")
        self.min_margin = default_margin if min_margin is None else min_margin
        self.k_on, self.hold, self.refractory = k_on, hold, refractory
        self.baseline_sec = baseline_sec
        self.manual_on = manual_on
        self.base_vals = []
        self.base_t0 = None
        self.mu, self.sd = 0.0, 0.02
        self.on_thr = manual_on if manual_on else 1.1
        self.off_thr = 0.0
        self.arm_t = None
        self.last_fire = -1e9
        self.value = 0.0
        self.state = "기준선 측정"

    def _signal(self, blend):
        if self.exclusive:
            other = "eyeBlinkRight" if self.name == "winkleft" else "eyeBlinkLeft"
            return max(0.0, blend.get(self.keys[0], 0.0) - blend.get(other, 0.0))
        if self.name == "longblink":
            return min(blend.get(k, 0.0) for k in self.keys)
        return float(np.mean([blend.get(k, 0.0) for k in self.keys]))

    def recalibrate(self):
        self.base_vals, self.base_t0 = [], None
        self.state = "기준선 측정"

    def update(self, t, ctx):
        v = self._signal(ctx["blend"])
        self.value = v

        if self.base_t0 is None:
            self.base_t0 = t
        if t - self.base_t0 < self.baseline_sec:
            self.base_vals.append(v)
            self.state = "기준선 측정"
            return None
        if self.base_vals:
            arr = np.array(self.base_vals)
            self.mu = float(np.median(arr))
            self.sd = float(max(np.std(arr), 0.01))
            auto = self.mu + max(self.k_on * self.sd, self.min_margin)
            self.on_thr = self.manual_on if self.manual_on else min(auto, 0.92)
            self.off_thr = self.mu + 0.45 * (self.on_thr - self.mu)
            self.base_vals = []

        if t - self.last_fire < self.refractory:
            self.state = "대기(불응기)"
            self.arm_t = None
            return None

        if self.arm_t is None:
            if v >= self.on_thr:
                self.arm_t = t          # 조준 시점: 이 순간의 커서 좌표에 클릭한다
                self.state = "조준"
            else:
                self.state = "감시"
            return None

        if v < self.off_thr:            # 임계값 아래로 완전히 떨어지면 취소
            self.arm_t = None
            self.state = "감시"
            return None
        if t - self.arm_t >= self.hold:
            fired = self.arm_t
            self.arm_t = None
            self.last_fire = t
            self.state = "클릭"
            return fired
        self.state = "조준"
        return None


class NodDetector:
    """끄덕임 클릭. 아래로 내렸다가 되돌아오는 왕복만 인정한다."""

    def __init__(self, amp=0.055, window=0.75, refractory=1.0, baseline_sec=3.0):
        self.amp, self.window, self.refractory = amp, window, refractory
        self.baseline_sec = baseline_sec
        self.base_vals, self.base_t0 = [], None
        self.mu = 0.0
        self.down_t = None
        self.last_fire = -1e9
        self.value = 0.0
        self.on_thr = amp
        self.state = "기준선 측정"

    def recalibrate(self):
        self.base_vals, self.base_t0 = [], None
        self.state = "기준선 측정"

    def update(self, t, ctx):
        raw = ctx["head_y"]
        if self.base_t0 is None:
            self.base_t0 = t
        if t - self.base_t0 < self.baseline_sec:
            self.base_vals.append(raw)
            self.state = "기준선 측정"
            return None
        if self.base_vals:
            self.mu = float(np.median(self.base_vals))
            self.base_vals = []

        dev = raw - self.mu             # 고개를 숙이면 코가 눈 축에서 멀어진다
        self.value = dev
        if t - self.last_fire < self.refractory:
            self.state = "대기(불응기)"
            self.down_t = None
            return None

        if self.down_t is None:
            if dev >= self.amp:
                self.down_t = t
                self.state = "숙임 감지"
            else:
                self.state = "감시"
            return None

        if t - self.down_t > self.window:
            self.down_t = None          # 숙인 채로 멈춘 것은 끄덕임이 아니다
            self.state = "감시"
            return None
        if dev <= self.amp * 0.35:      # 되돌아옴 = 끄덕임 완성
            fired = self.down_t
            self.down_t = None
            self.last_fire = t
            self.state = "클릭"
            return fired
        self.state = "숙임 감지"
        return None


class DwellDetector:
    """머무름 클릭. 표정을 못 쓰는 사용자를 위한 최후 수단."""

    def __init__(self, radius=28.0, wait=1.1, refractory=1.0):
        self.radius, self.wait, self.refractory = radius, wait, refractory
        self.anchor = None
        self.anchor_t = 0.0
        self.last_fire = -1e9
        self.escaped = True
        self.value = 0.0
        self.on_thr = wait
        self.state = "감시"

    def recalibrate(self):
        self.anchor = None

    def update(self, t, ctx):
        pos = np.array(ctx["cursor"])
        if self.anchor is None or np.linalg.norm(pos - self.anchor) > self.radius:
            self.anchor, self.anchor_t = pos, t
            self.escaped = True
            self.value = 0.0
            self.state = "감시"
            return None
        held = t - self.anchor_t
        self.value = held
        if t - self.last_fire < self.refractory or not self.escaped:
            self.state = "대기(불응기)"
            return None
        if held >= self.wait:
            self.last_fire = t
            self.escaped = False
            self.state = "클릭"
            return self.anchor_t
        self.state = f"머무름 {held:.1f}s"
        return None


def make_detector(name, args):
    if name == "none":
        return None
    if name == "nod":
        return NodDetector(amp=args.nod_amp, refractory=args.refractory)
    if name == "dwell":
        return DwellDetector(wait=args.dwell_wait, refractory=args.refractory)
    return ThresholdDetector(name, k_on=args.k_on, hold=args.hold,
                             refractory=args.refractory, manual_on=args.on_thresh)


# ---------------------------------------------------------------- 추적기

class FaceTracker:
    def __init__(self):
        if not MODEL_PATH.exists():
            raise SystemExit(f"모델 파일이 없습니다: {MODEL_PATH}")
        opts = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(MODEL_PATH),
                                               delegate=mp_python.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            min_face_detection_confidence=0.3,
            min_face_presence_confidence=0.3,
            min_tracking_confidence=0.3,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(opts)
        self._t0 = time.time()
        self._last_ts = -1

    def process(self, frame_rgb, ts_ms=None):
        # mediapipe VIDEO 모드는 단조 증가 타임스탬프를 요구한다.
        # 캘리브레이션과 본 루프가 같은 추적기를 쓰므로 여기서 한 곳으로 관리한다.
        ts = int((time.time() - self._t0) * 1000) if ts_ms is None else int(ts_ms)
        ts = max(ts, self._last_ts + 1)
        self._last_ts = ts
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        res = self.landmarker.detect_for_video(image, ts)
        if not res.face_landmarks:
            return None, {}
        blend = {}
        if res.face_blendshapes:
            blend = {c.category_name: c.score for c in res.face_blendshapes[0]}
        return res.face_landmarks[0], blend

    def close(self):
        self.landmarker.close()


class Camera:
    def __init__(self, index, width=1920, height=1080, flip=True):
        self.cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            raise SystemExit(f"카메라 {index}를 열지 못했습니다.")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.flip = flip

    def read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        if self.flip:
            frame = cv2.flip(frame, 1)
        return frame

    def release(self):
        self.cap.release()


def screen_size():
    from Quartz import CGDisplayBounds, CGMainDisplayID
    b = CGDisplayBounds(CGMainDisplayID())
    return float(b.size.width), float(b.size.height)


# ---------------------------------------------------------------- 캘리브레이션

CAL_POINTS = [(x, y) for y in (0.10, 0.37, 0.63, 0.90)
              for x in (0.08, 0.36, 0.64, 0.92)]


def run_calibration(cam, tracker, mode, sw, sh, settle=0.8, collect=1.0, fix_head=False):
    win = "SeeOSK 캘리브레이션"
    cv2.namedWindow(win, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    canvas_w, canvas_h = int(sw), int(sh)

    samples, targets = [], []
    live = deque(maxlen=5)
    jitter = []
    ref = [None, None]
    t_start = time.time()
    order = list(range(len(CAL_POINTS)))

    for n, pi in enumerate(order):
        fx, fy = CAL_POINTS[pi]
        cx, cy = int(fx * canvas_w), int(fy * canvas_h)
        phase_t0 = time.time()
        bucket = []
        while True:
            frame = cam.read()
            if frame is None:
                continue
            h, w = frame.shape[:2]
            lm, _ = tracker.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            elapsed = time.time() - phase_t0
            collecting = elapsed >= settle

            f = extract_features(lm, w, h) if lm is not None else None
            if collecting and f and f["has_iris"]:
                bucket.append(raw_vector(f))
            if f:
                live.append((f["head_x"], f["head_y"]))

            canvas = np.full((canvas_h, canvas_w, 3), 18, np.uint8)
            prog = 0.0 if not collecting else min(1.0, (elapsed - settle) / collect)
            color = (90, 200, 255) if not collecting else (110, 255, 140)
            cv2.circle(canvas, (cx, cy), 34, (55, 55, 55), 2)
            cv2.circle(canvas, (cx, cy), max(4, int(34 * (1 - prog))), color, -1)
            cv2.circle(canvas, (cx, cy), 6, (255, 255, 255), -1)
            if fix_head:
                tip = "머리는 그대로 두고 눈동자만 점으로 옮기세요"
            elif mode in ("iris", "gaze"):
                tip = "점을 바라보세요. 고개는 자연스럽게 살짝 따라가도 됩니다"
            else:
                tip = "점을 바라보며 고개를 그쪽으로 돌리세요"
            msg = f"{n + 1}/{len(CAL_POINTS)}  {tip}   (Esc 취소)"
            draw_text(canvas, msg, (60, canvas_h - 76), 26, (205, 205, 205))

            # 머리 방향 실시간 표시 — 점을 옮길 때마다 이 표식도 따라 움직여야 한다.
            bx, by, bw, bh = 60, 60, 220, 150
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), (70, 70, 70), 1)
            draw_text(canvas, "머리 방향 (고정 유지)" if fix_head else "머리 방향",
                      (bx, by - 30), 19, (150, 150, 150))
            if live:
                hx0, hy0 = live[-1]
                if ref[0] is None:
                    ref[0], ref[1] = hx0, hy0
                mx = int(bx + bw / 2 + np.clip((hx0 - ref[0]) / 0.12, -1, 1) * bw / 2)
                my = int(by + bh / 2 + np.clip((hy0 - ref[1]) / 0.12, -1, 1) * bh / 2)
                cv2.line(canvas, (bx + bw // 2, by), (bx + bw // 2, by + bh), (55, 55, 55), 1)
                cv2.line(canvas, (bx, by + bh // 2), (bx + bw, by + bh // 2), (55, 55, 55), 1)
                cv2.circle(canvas, (mx, my), 9, (110, 220, 255), -1)
            if lm is None:
                draw_text(canvas, "얼굴 미검출", (bx, by + bh + 12), 24, (255, 120, 120))
            cv2.imshow(win, canvas)
            if cv2.waitKey(1) & 0xFF == 27:
                cv2.destroyWindow(win)
                return None
            if collecting and elapsed - settle >= collect:
                break

        if len(bucket) < 5:
            cv2.destroyWindow(win)
            raise SystemExit("표본이 부족합니다. 조명과 얼굴 검출 상태를 확인하고 다시 실행하세요.")
        stack = np.stack(bucket)
        samples.append(np.median(stack, axis=0))
        jitter.append(float(np.mean(np.std(stack, axis=0))))
        targets.append([fx * sw, fy * sh])

    cv2.destroyWindow(win)
    raw = np.stack(samples)
    spread = (raw.max(axis=0) - raw.min(axis=0))
    print("특징 변화폭: " + "  ".join(f"{n}={v:.4f}" for n, v in zip(RAW_KEYS, spread)))
    print(f"점별 표본 흔들림: 평균 {np.mean(jitter):.4f} (작을수록 안정)")
    if fix_head and max(spread[0], spread[1]) > 0.08:
        print(f"주의: 머리 고정 조건인데 머리가 {max(spread[0], spread[1]):.3f}만큼 움직였습니다. "
              "눈동자 단독 성능이 과대평가될 수 있습니다.")

    print("\n포인팅 방식 비교 (같은 데이터, LOO 교차검증)")
    print(f"  {'방식':<10}{'LOO 평균':>12}{'LOO 95%':>11}{'훈련잔차':>11}")
    for name, lm_, lp, tr, rg in compare_modes(samples, targets):
        mark = "  ←" if name == mode else ""
        print(f"  {name:<10}{lm_:>10.0f}px{lp:>9.0f}px{tr:>9.0f}px{mark}")

    fit = fit_mapping(list(select(raw, mode)), targets)
    return {
        "mode": mode,
        "n_points": len(CAL_POINTS),
        "screen": [sw, sh],
        "weights": fit["weights"].tolist(),
        "mean": fit["mean"].tolist(),
        "std": fit["std"].tolist(),
        "ridge": fit["ridge"],
        "feature_spread": spread.tolist(),
        "raw_samples": [v.tolist() for v in samples],
        "targets": targets,
        "mean_error_px": fit["mean_error_px"],
        "p95_error_px": fit["p95_error_px"],
        "loo_mean_px": fit["loo_mean_px"],
        "loo_p95_px": fit["loo_p95_px"],
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def profile_path(camera_name, mode, fix_head=False):
    PROFILE_DIR.mkdir(exist_ok=True)
    suffix = "__fixedhead" if fix_head else ""
    return PROFILE_DIR / f"{safe_name(camera_name)}__{mode}{suffix}.json"


def load_profile(camera_name, mode, fix_head=False):
    p = profile_path(camera_name, mode, fix_head)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    if data.get("n_points") != len(CAL_POINTS) or "raw_samples" not in data \
            or data.get("mode") != mode:
        print("이전 프로파일의 형식이 달라 다시 캘리브레이션합니다.")
        return None
    for k in ("weights", "mean", "std"):
        data[k] = np.array(data[k])
    return data


def save_profile(camera_name, mode, data, fix_head=False):
    p = profile_path(camera_name, mode, fix_head)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return p


# ---------------------------------------------------------------- 한글 렌더링

_KO_FONT = None
for _cand in ("/System/Library/Fonts/AppleSDGothicNeo.ttc",
              "/System/Library/Fonts/Supplemental/AppleGothic.ttf"):
    if Path(_cand).exists():
        _KO_FONT = _cand
        break

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = _KO_FONT is not None
except ImportError:
    _PIL_OK = False

_font_cache = {}


def draw_text(img, text, org, size=20, color=(235, 235, 235)):
    """OpenCV는 한글을 못 그리므로 PIL로 합성한다."""
    if not _PIL_OK:
        cv2.putText(img, text.encode("ascii", "replace").decode(), org,
                    cv2.FONT_HERSHEY_SIMPLEX, size / 28.0, color[::-1], 1, cv2.LINE_AA)
        return img
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(_KO_FONT, size)
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    ImageDraw.Draw(pil).text(org, text, font=_font_cache[size], fill=color)
    img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    return img


# ---------------------------------------------------------------- 메인 루프

def run(args):
    from pynput.mouse import Button, Controller

    index, cam_name = resolve_camera(args.camera)
    sw, sh = screen_size()
    print(f"카메라: [{index}] {cam_name}   화면: {int(sw)}x{int(sh)}   포인팅: {args.pointer}")

    cam = Camera(index, args.width, args.height, flip=not args.no_flip)
    tracker = FaceTracker()
    mouse = Controller()

    profile = None if args.calibrate else load_profile(cam_name, args.pointer, args.fix_head)
    if profile is None:
        print("캘리브레이션을 시작합니다. 화면의 점을 차례로 바라보세요.")
        profile = run_calibration(cam, tracker, args.pointer, sw, sh, fix_head=args.fix_head)
        if profile is None:
            cam.release(); tracker.close()
            print("취소했습니다.")
            return
        path = save_profile(cam_name, args.pointer, profile, args.fix_head)
        print(f"프로파일 저장: {path}")
        for k in ("weights", "mean", "std"):
            profile[k] = np.array(profile[k])
    print(f"캘리브레이션 — 훈련잔차 {profile['mean_error_px']:.0f}px / "
          f"실제추정(LOO) 평균 {profile['loo_mean_px']:.0f}px · 95% {profile['loo_p95_px']:.0f}px")

    fx_filter, fy_filter = OneEuro(args.mincutoff, args.beta), OneEuro(args.mincutoff, args.beta)
    left = make_detector(args.click, args)
    right = make_detector(args.rclick, args)

    history = deque(maxlen=240)          # (시각, x, y) — 조준 시점 좌표 복원용
    paused = args.start_paused
    t_start = time.time()
    last_t = time.time()
    fps = 0.0
    cursor = np.array([sw / 2, sh / 2])
    win = "SeeOSK 카메라 마우스"

    def latched_position(arm_t):
        """클릭은 '조준을 시작한 순간'의 좌표에 찍는다. 제스처 중 흔들림을 배제한다."""
        target = arm_t - args.preroll
        best = None
        for ts, x, y in history:
            if ts <= target:
                best = (x, y)
            else:
                break
        return best if best is not None else tuple(cursor)

    def do_click(button, arm_t):
        x, y = latched_position(arm_t)
        if args.dry_run:
            print(f"[모의] {button} 클릭 @ ({x:.0f}, {y:.0f})")
            return
        mouse.position = (x, y)
        mouse.click(button, 1)

    try:
        while True:
            frame = cam.read()
            if frame is None:
                continue
            now = time.time()
            dt = max(now - last_t, 1e-3)
            last_t = now
            fps = 0.9 * fps + 0.1 * (1.0 / dt)

            h, w = frame.shape[:2]
            lm, blend = tracker.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            feats = extract_features(lm, w, h) if lm is not None else None

            armed = False
            if feats is not None:
                v = feature_vector(feats, args.pointer)
                raw = apply_mapping(profile, v)
                sx = float(np.clip(fx_filter(raw[0], dt), 0, sw - 1))
                sy = float(np.clip(fy_filter(raw[1], dt), 0, sh - 1))
                ctx = {"blend": blend, "head_y": feats["head_y"],
                       "cursor": (sx, sy), "dt": dt}

                fired_l = left.update(now, ctx) if left else None
                fired_r = right.update(now, ctx) if right else None
                armed = any(d and d.state in ("조준", "숙임 감지") for d in (left, right))

                # 조준 중에는 커서를 세워 둔다. 표정·끄덕임이 만드는 미세 이동이 오클릭의 주범이다.
                if not (args.freeze and armed):
                    cursor = np.array([sx, sy])
                    history.append((now, sx, sy))
                    if not paused and not args.no_move:
                        try:
                            mouse.position = (sx, sy)
                        except Exception as exc:
                            print(f"커서 이동 실패({exc}). 손쉬운 사용 권한을 확인하세요.")
                            args.no_move = True

                if not paused:
                    if fired_l:
                        do_click(Button.left, fired_l)
                    if fired_r:
                        do_click(Button.right, fired_r)

            # --- 미리보기 HUD
            view = cv2.resize(frame, (640, int(640 * h / w)))
            if lm is not None:
                for i in (L_EYE_OUT, L_EYE_IN, R_EYE_IN, R_EYE_OUT, NOSE_TIP):
                    p = (int(lm[i].x * 640), int(lm[i].y * view.shape[0]))
                    cv2.circle(view, p, 2, (120, 255, 160), -1)
            banner = (40, 40, 40) if not paused else (25, 25, 120)
            if armed:
                banner = (20, 110, 160)
            cv2.rectangle(view, (0, 0), (640, 96), banner, -1)
            state_l = f"{args.click}: {left.state}" if left else "왼쪽 클릭 없음"
            state_r = f"{args.rclick}: {right.state}" if right else ""
            draw_text(view, f"{'일시정지' if paused else '동작중'}   {fps:4.1f} fps   "
                            f"커서 ({cursor[0]:.0f},{cursor[1]:.0f})", (12, 8), 17)
            draw_text(view, state_l, (12, 34), 17, (150, 255, 190))
            if state_r:
                draw_text(view, state_r, (12, 58), 17, (255, 210, 150))
            if left and hasattr(left, "on_thr"):
                bar_w = int(np.clip(left.value / max(left.on_thr, 1e-3), 0, 1.4) * 300)
                cv2.rectangle(view, (330, 36), (330 + min(bar_w, 300), 52), (120, 230, 160), -1)
                cv2.rectangle(view, (330, 36), (630, 52), (200, 200, 200), 1)
                cv2.line(view, (630, 32), (630, 56), (80, 80, 255), 2)
            draw_text(view, "Esc 종료 · p 일시정지 · b 기준선 재측정 · c 재캘리브레이션",
                      (12, view.shape[0] - 26), 15, (190, 190, 190))
            cv2.imshow(win, view)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            if key == ord("p"):
                paused = not paused
            if key == ord("b"):
                for d in (left, right):
                    if d:
                        d.recalibrate()
            if key == ord("c"):
                cv2.destroyWindow(win)
                newp = run_calibration(cam, tracker, args.pointer, sw, sh, fix_head=args.fix_head)
                if newp:
                    save_profile(cam_name, args.pointer, newp, args.fix_head)
                    for k in ("weights", "mean", "std"):
                        newp[k] = np.array(newp[k])
                    profile = newp
                    print(f"재캘리브레이션 완료: 평균 {newp['mean_error_px']:.0f}px")
    finally:
        cam.release()
        tracker.close()
        cv2.destroyAllWindows()



# ---------------------------------------------------------------- 진단 모드

def run_diag(args):
    """창을 띄우지 않고 카메라·검출·표정 신호를 수치로 점검한다."""
    index, cam_name = resolve_camera(args.camera)
    cam = Camera(index, args.width, args.height, flip=not args.no_flip)
    tracker = FaceTracker()
    print(f"카메라: [{index}] {cam_name} · {args.diag:.0f}초 측정 — 화면을 정면으로 바라보세요")

    t0 = time.time()
    frames = hits = 0
    iris_ok = 0
    hx, hy, fw, gx, gy, ew = [], [], [], [], [], []
    blend_acc = {}
    try:
        while time.time() - t0 < args.diag:
            frame = cam.read()
            if frame is None:
                continue
            frames += 1
            h, w = frame.shape[:2]
            lm, blend = tracker.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if lm is None:
                continue
            hits += 1
            f = extract_features(lm, w, h)
            if not f:
                continue
            iris_ok += 1 if f["has_iris"] else 0
            hx.append(f["head_x"]); hy.append(f["head_y"]); fw.append(f["face_w"])
            if f["has_iris"]:
                gx.append(f["gaze_x"]); gy.append(f["gaze_y"])
                ew.append(abs(lm[L_EYE_OUT].x - lm[L_EYE_IN].x) * w)
            for k, v in blend.items():
                blend_acc.setdefault(k, []).append(v)
    finally:
        cam.release(); tracker.close()

    fps = frames / max(time.time() - t0, 1e-6)
    print(f"\n프레임 {frames}개 · {fps:.1f} fps · 해상도 {w}x{h}")
    print(f"얼굴 검출률 {100 * hits / max(frames, 1):.1f}%   홍채 랜드마크 {100 * iris_ok / max(hits, 1):.1f}%")
    if not hx:
        print("얼굴을 찾지 못했습니다. 조명과 카메라 방향을 확인하세요.")
        return
    for label, arr in (("head_x", hx), ("head_y", hy), ("gaze_x", gx), ("gaze_y", gy)):
        a = np.array(arr)
        print(f"{label}: 중앙값 {np.median(a):+.4f}  표준편차 {np.std(a):.4f}  "
              f"범위 {a.min():+.4f}~{a.max():+.4f}")
    print(f"얼굴 폭: 중앙값 {np.median(fw):.0f}px  (카메라와의 거리 지표)")
    if ew:
        print(f"눈 가로 폭: 중앙값 {np.median(ew):.1f}px  ← 눈동자 방식의 분해능을 좌우")
        print(f"정지 시 gaze_x 잡음(σ): {np.std(gx):.5f}  ·  gaze_y 잡음(σ): {np.std(gy):.5f}")

    print("\n표정 신호(정지 기준선) — 임계값 = 중앙값 + k×표준편차")
    rows = []
    for name in ("jawOpen", "browInnerUp", "browOuterUpLeft", "mouthSmileLeft",
                 "mouthPucker", "cheekPuff", "eyeBlinkLeft", "eyeBlinkRight"):
        a = np.array(blend_acc.get(name, [0.0]))
        thr = min(np.median(a) + max(args.k_on * np.std(a), 0.30), 0.92)
        rows.append((name, float(np.median(a)), float(np.std(a)), float(a.max()), float(thr)))
    print(f"  {'신호':<18}{'중앙값':>8}{'표준편차':>10}{'최대':>8}{'자동임계':>10}")
    for n, med, sd, mx, thr in rows:
        flag = "  ← 기준선이 높음" if med > 0.25 else ""
        print(f"  {n:<18}{med:>8.3f}{sd:>10.3f}{mx:>8.3f}{thr:>10.3f}{flag}")

    jitter_px = float(np.std(hx)) * 1000
    print(f"\n정지 시 head_x 떨림 ≈ {jitter_px:.1f} (화면 1000px 환산 · 1€ 필터 적용 전)")


def run_compare(args):
    _, cam_name = resolve_camera(args.camera)
    found = sorted(PROFILE_DIR.glob(f"{safe_name(cam_name)}__*.json"))
    found = [f for f in found if "raw_samples" in json.loads(f.read_text())]
    if not found:
        raise SystemExit("저장된 캘리브레이션이 없습니다. 먼저 한 번 실행하세요.")
    for f in found:
        _report_profile(f)


def _report_profile(path):
    d = json.loads(path.read_text())
    raw = [np.array(v) for v in d["raw_samples"]]
    print(f"\n=== {path.name} · 점 {len(raw)}개 · {d['created']}")
    print("특징 변화폭: " + "  ".join(
        f"{n}={v:.4f}" for n, v in zip(RAW_KEYS, d["feature_spread"])))
    print(f"\n  {'방식':<10}{'LOO 평균':>12}{'LOO 95%':>11}{'훈련잔차':>11}{'릿지':>9}")
    for name, lm_, lp, tr, rg in compare_modes(raw, d["targets"]):
        print(f"  {name:<10}{lm_:>10.0f}px{lp:>9.0f}px{tr:>9.0f}px{rg:>9.0e}")
    sw = d["screen"][0]
    best = compare_modes(raw, d["targets"])[0]
    print(f"\n최적 {best[0]}: 평균 오차가 화면 가로폭의 {100*best[1]/sw:.1f}%")


def main():
    ap = argparse.ArgumentParser(description="SeeOSK 카메라 마우스")
    ap.add_argument("--list", action="store_true", help="연결된 카메라 목록")
    ap.add_argument("--diag", type=float, metavar="초", help="창 없이 검출·신호 품질 점검")
    ap.add_argument("--camera", help="카메라 인덱스 또는 이름 일부")
    ap.add_argument("--pointer", choices=list(MODES), default="head",
                    help="head=머리 자세 · iris=눈동자만 · gaze=눈동자+머리 · all=전부")
    ap.add_argument("--fix-head", action="store_true",
                    help="머리를 고정하고 눈동자만 움직이는 조건으로 캘리브레이션")
    ap.add_argument("--compare", action="store_true",
                    help="저장된 캘리브레이션으로 포인팅 방식들을 다시 비교")
    ap.add_argument("--click", choices=ALL_GESTURES, default="jawopen")
    ap.add_argument("--rclick", choices=ALL_GESTURES, default="none")
    ap.add_argument("--calibrate", action="store_true", help="프로파일 무시하고 재캘리브레이션")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--no-flip", action="store_true", help="좌우 반전 끄기")
    ap.add_argument("--no-move", action="store_true", help="커서를 움직이지 않고 진단만")
    ap.add_argument("--dry-run", action="store_true", help="클릭을 실제로 하지 않음")
    ap.add_argument("--start-paused", action="store_true")
    ap.add_argument("--freeze", action="store_true", default=True,
                    help="조준 중 커서 고정(기본 켜짐)")
    ap.add_argument("--no-freeze", dest="freeze", action="store_false")
    ap.add_argument("--k-on", type=float, default=6.0, help="임계값 = 기준선 + k×표준편차")
    ap.add_argument("--on-thresh", type=float, help="임계값 수동 고정(0~1)")
    ap.add_argument("--hold", type=float, default=0.12, help="유지 시간(초)")
    ap.add_argument("--refractory", type=float, default=0.9, help="불응기(초)")
    ap.add_argument("--preroll", type=float, default=0.05, help="조준 시점에서 더 거슬러 갈 시간(초)")
    ap.add_argument("--nod-amp", type=float, default=0.055)
    ap.add_argument("--dwell-wait", type=float, default=1.1)
    ap.add_argument("--mincutoff", type=float, default=1.2)
    ap.add_argument("--beta", type=float, default=0.02)
    args = ap.parse_args()

    if args.list:
        list_cameras()
        return
    if args.diag:
        run_diag(args)
        return
    if args.compare:
        run_compare(args)
        return
    run(args)


if __name__ == "__main__":
    main()
