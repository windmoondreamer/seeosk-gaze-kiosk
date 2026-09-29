#!/usr/bin/env python3
"""Local JSON-lines worker. Camera frames never leave the process except preview."""
import os
import base64
import hashlib
import json
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

PROTOCOL = sys.stdout
if __name__ == '__main__':
    sys.stdout = sys.stderr  # Third-party libraries cannot corrupt the JSON stream.
    import faulthandler
    faulthandler.enable()
    faulthandler.dump_traceback_later(15, repeat=False)
    print('Loading camera libraries...', flush=True)
import cv2
import numpy as np
from core import (CAL_POINTS, CHECK_POINTS, fit, predict, validate, head_vector,
                  head_ok, stable_summary, save_profile, load_profile,
                  fit_gaze_zones, classify_gaze_zone)
from legacy_gaze import (FaceTracker, extract_features, OneEuro, ThresholdDetector,
                         NodDetector, BLEND_GESTURES)
from head_pointer import HeadPointer
from platform_io import camera_devices, open_camera, user_data_dir
from preview import blur_background
if __name__ == '__main__':
    faulthandler.cancel_dump_traceback_later()
    print('Camera libraries loaded.', flush=True)

ROOT = Path(os.environ.get("SEE_OSK_RESOURCES", Path(__file__).resolve().parent.parent))
DATA = user_data_dir()
DATA.mkdir(parents=True, exist_ok=True)
OUTPUT_LOCK = threading.Lock()


def emit(kind, **data):
    with OUTPUT_LOCK:
        try:
            PROTOCOL.write(json.dumps(dict(type=kind, **data), ensure_ascii=False, allow_nan=False)+'\n')
            PROTOCOL.flush()
        except (BrokenPipeError, OSError):
            pass


def devices():
    return [dict(index=d['index'], name=d['name'], id=d['id'],
                 phone=d.get('phone', False), builtin=d.get('builtin', False))
            for d in camera_devices(cv2)]


class Detector:
    def __init__(self):
        self.jobs = queue.Queue(maxsize=1)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def submit(self, job):
        try:
            self.jobs.get_nowait()
        except queue.Empty:
            pass
        self.jobs.put_nowait(job)

    def run(self):
        model = None
        while True:
            job = self.jobs.get()
            try:
                if model is None:
                    from onnx_detector import OnnxDetector
                    model = OnnxDetector(ROOT/'assets/best.onnx')
                frame = cv2.imread(job['path'])
                if frame is None:
                    raise ValueError('화면 이미지를 읽지 못했습니다.')
                h, w = frame.shape[:2]
                t = time.monotonic()
                regions = []
                for x1, y1, x2, y2, confidence, cls in model.detect(frame, conf=.35):
                    if x2-x1 < 8 or y2-y1 < 8:
                        continue
                    regions.append(dict(x=x1/w, y=y1/h, w=(x2-x1)/w, h=(y2-y1)/h,
                                        confidence=confidence, label=model.names[cls]))
                emit('regions', request=job.get('request'), regions=regions,
                     inference_ms=round((time.monotonic()-t)*1000), source_size=[w, h])
            except Exception as ex:
                traceback.print_exc()
                emit('detector_error', request=job.get('request'), message=str(ex))
            finally:
                if job.get('temporary'):
                    Path(job['path']).unlink(missing_ok=True)


# macOS 포인터 설정처럼 동작마다 표정을 따로 지정합니다.
ACTIONS = ('left', 'right', 'double', 'drag', 'pause', 'recenter')
DEFAULT_GESTURES = {'left': 'jawopen'}
# 표정을 얼마나 크게 지어야 하는지. 1에 가까울수록 작은 움직임에도 반응합니다.
DEFAULT_GESTURE_SENSITIVITY = .65
# 표정별 기준 상한. 윙크는 좌우 차이를, 미소는 양쪽 평균을 쓰므로 원래 값이 너무 높았습니다.
HARD_MARGIN_CAP = {'smile': .16, 'winkleft': .22, 'winkright': .22,
                   'longblink': .28, 'pucker': .20, 'cheekpuff': .16, 'browup': .20}


def clamp_sensitivity(value, fallback=DEFAULT_GESTURE_SENSITIVITY):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    if value != value or value in (float('inf'), float('-inf')):
        return fallback
    return max(0., min(1., value))


def expression_thresholds(sensitivity):
    """표정별 대략적인 클릭 기준값. 막대 색을 실제 설정과 맞추기 위한 값입니다.

    실제 발사 기준은 여기에 시작 직후 잰 평상시 값이 더해지므로 정확히 같지는 않습니다.
    """
    values = {}
    for name in BLEND_GESTURES:
        detector = make_detector(name, sensitivity)
        values[name] = round(float(detector.min_margin), 3)
    return values


def expression_levels(blend):
    """표정 9종의 현재 신호값. 어떤 표정이 실제로 잡히는지 눈으로 보기 위한 값입니다."""
    levels = {}
    for name, (keys, _margin) in BLEND_GESTURES.items():
        if name in ('winkleft', 'winkright'):
            other = 'eyeBlinkRight' if name == 'winkleft' else 'eyeBlinkLeft'
            value = max(0., blend.get(keys[0], 0.)-blend.get(other, 0.))
        elif name == 'longblink':
            value = min(blend.get(k, 0.) for k in keys)
        else:
            value = sum(blend.get(k, 0.) for k in keys)/len(keys)
        levels[name] = round(float(value), 3)
    return levels


def make_detector(name, sensitivity=DEFAULT_GESTURE_SENSITIVITY):
    """표정 이름으로 검출기를 만듭니다. 쓰지 않는 동작은 None.

    민감도는 기준선 위로 얼마나 올라와야 인정할지를 줄입니다. 기본 임계값은 입을 크게
    벌려야 넘는 값이라, 조금만 벌려도 눌리게 하려면 이 여유를 줄여야 합니다.
    """
    sensitivity = clamp_sensitivity(sensitivity)
    margin_scale = 1-.9*sensitivity      # 0.65에서 0.30 -> 0.124
    noise_scale = 1-.7*sensitivity       # 잡음 기준(k_on*sd)도 함께 낮춥니다
    if name in BLEND_GESTURES:
        # 미소·윙크는 원래 기준이 높아 조금 지어서는 넘지 못했습니다. 표정마다 상한을 둡니다.
        base = min(BLEND_GESTURES[name][1], HARD_MARGIN_CAP.get(name, .30))
        return ThresholdDetector(name, k_on=6.*noise_scale,
                                 min_margin=max(.03, base*margin_scale),
                                 hold=.10)
    if name == 'nod':
        return NodDetector(amp=max(.02, .055*noise_scale))
    return None


class Engine:
    def __init__(self):
        self.head_pointer = HeadPointer()
        self.gestures = dict(DEFAULT_GESTURES)
        self.gesture_sensitivity = DEFAULT_GESTURE_SENSITIVITY
        self.detectors = {}
        self.rebuild_detectors()
        self.blur_preview = True
        self.commands = queue.Queue()
        self.cap = None
        self.tracker = None
        self.camera = None
        self.geometry = None
        self.profile = None
        self.backup = None
        self.candidate = None
        self.calibration = False
        self.samples, self.targets, self.spreads, self.checks, self.check_targets = [], [], [], [], []
        self.reference = None
        self.collection = None
        self.generation = 0
        self.filters = [OneEuro(1.5, .008), OneEuro(1.5, .008)]
        self.detector = Detector()
        self.running = True
        self.last_frame = time.monotonic()
        self.last_preview = 0
        self.failures = 0

    def rebuild_detectors(self):
        built = {a: make_detector(g, self.gesture_sensitivity) for a, g in self.gestures.items()}
        self.detectors = {a: d for a, d in built.items() if d is not None}
        self.gestures = {a: g for a, g in self.gestures.items() if a in self.detectors}

    def recalibrate_gestures(self):
        for detector in self.detectors.values():
            detector.recalibrate()

    def profile_path(self):
        key = hashlib.sha256(self.camera['id'].encode()).hexdigest()[:16]
        return DATA/'profiles'/f'{key}.json'

    def reset_filters(self):
        self.filters = [OneEuro(1.5, .008), OneEuro(1.5, .008)]

    def close_camera(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.tracker is not None:
            self.tracker.close()
            self.tracker = None
        self.collection = None
        self.calibration = False
        self.profile = None
        self.head_pointer.reset()
        self.reset_filters()

    def handle(self, c):
        cmd = c.get('cmd')
        if cmd == 'shutdown':
            self.running = False
        elif cmd == 'configure':
            geometry = c['geometry']
            if geometry != self.geometry:
                self.geometry = geometry
                self.collection = None
                if self.calibration:
                    self.calibration = False
                    self.profile = self.backup
                    self.backup = None
                self.reset_filters()
        elif cmd == 'start':
            self.close_camera()
            found = devices()
            emit('cameras', devices=found)
            self.camera = next((d for d in found if d['index'] == int(c['camera'])), None)
            if int(c['camera']) < 0:
                self.camera = next((d for d in found if d['builtin']), next((d for d in found if not d['phone']), None))
            if self.camera is None:
                raise ValueError('선택한 카메라가 연결되어 있지 않습니다.')
            try:
                self.cap = open_camera(self.camera['index'], 1280, 720, 30, cv2)
            except RuntimeError as ex:
                self.close_camera()
                raise ValueError(f'{ex} 카메라 권한과 다른 앱의 사용 여부를 확인해 주세요.')
            self.tracker = FaceTracker()
            self.recalibrate_gestures()
            self.failures = 0
            emit('started', camera=self.camera['name'])
        elif cmd == 'stop':
            self.close_camera()
            emit('stopped')
        elif cmd == 'recenter':
            self.head_pointer.reset()
            self.recalibrate_gestures()
            emit('centering')
        elif cmd == 'gestures':
            requested = c.get('map') or {}
            self.gestures = {a: requested[a] for a in ACTIONS
                             if requested.get(a) and requested[a] != 'none'}
            self.rebuild_detectors()
            emit('gestures', map=self.gestures, available=list(BLEND_GESTURES)+['nod'],
                 thresholds=expression_thresholds(self.gesture_sensitivity))
        elif cmd == 'gesture_sensitivity':
            self.gesture_sensitivity = clamp_sensitivity(c.get('value'), self.gesture_sensitivity)
            self.rebuild_detectors()
            emit('gesture_sensitivity', value=self.gesture_sensitivity,
                 thresholds=expression_thresholds(self.gesture_sensitivity))
        elif cmd == 'blur_preview':
            self.blur_preview = bool(c.get('value', True))
            emit('blur_preview', value=self.blur_preview)
        elif cmd == 'sensitivity':
            self.head_pointer.sensitivity = c.get('value')
            emit('sensitivity', value=self.head_pointer.sensitivity)
        elif cmd == 'detect':
            self.detector.submit(c)
        elif cmd == 'list_cameras':
            emit('cameras', devices=devices())

    def frame(self):
        now = time.monotonic()
        ok, frame = self.cap.read()
        if not ok or frame is None:
            self.failures += 1
            emit('tracking', valid=False, reason='카메라 프레임 대기 중')
            if self.failures >= 30:
                self.close_camera()
                emit('stopped')
                emit('error', message='카메라 연결이 끊겼습니다. 다시 연결하고 시작해 주세요.')
            time.sleep(.05)
            return
        self.failures = 0
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        lm, blend = self.tracker.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        features = extract_features(lm, w, h, include_iris=False) if lm is not None else None
        valid = bool(features and features['face_w'] >= 70)
        dt = max(.001, now-self.last_frame)
        self.last_frame = now
        data = dict(valid=False, fps=round(min(60., 1/dt), 1),
                    reason='얼굴을 카메라 정면에 두세요.')
        if valid and self.geometry:
            was_ready = self.head_pointer.reference is not None
            xy = self.head_pointer.update(features['head_x'], features['head_y'], now,
                                          self.geometry['width'], self.geometry['height'])
            if xy is None:
                data['reason'] = '정면을 보고 잠시 멈춰 주세요 · 중앙 맞추는 중'
            else:
                if not was_ready:
                    emit('head_ready')
                data.update(valid=True, x=xy[0], y=xy[1], reason='고개로 이동 · 입을 벌리면 선택')
        if valid:
            ctx = {'blend': blend, 'head_y': features['head_y']}
            levels = {}
            for action, detector in self.detectors.items():
                fired = detector.update(now, ctx)
                levels[action] = dict(gesture=self.gestures[action],
                                      value=round(float(detector.value), 3),
                                      state=detector.state)
                # 포인터 좌표가 필요한 동작은 추적이 유효할 때만 발사합니다.
                if fired is None:
                    continue
                if action in ('left', 'right', 'double', 'drag') and not data['valid']:
                    continue
                emit('gesture_action', action=action, gesture=self.gestures[action],
                     x=data.get('x'), y=data.get('y'))
            data['gestures'] = levels
            data['expressions'] = expression_levels(blend)
        else:
            for detector in self.detectors.values():
                # 얼굴이 없는 동안 조준 상태를 지웁니다.
                if hasattr(detector, 'arm_t'):
                    detector.arm_t = None
                if hasattr(detector, 'down_t'):
                    detector.down_t = None
        if now-self.last_preview > .25:
            preview = cv2.resize(frame, (320, int(h*320/w)))
            if self.blur_preview:
                preview = blur_background(preview, lm)
            ok, jpg = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 65])
            if ok:
                data['preview'] = base64.b64encode(jpg).decode()
            self.last_preview = now
        emit('tracking', **data)

    def run(self):
        def reader():
            for line in sys.stdin:
                try:
                    self.commands.put(json.loads(line))
                except ValueError:
                    emit('error', message='명령을 읽지 못했습니다.')
            self.commands.put({'cmd':'shutdown'})
        threading.Thread(target=reader, daemon=True).start()
        emit('ready')
        try:
            emit('cameras', devices=devices())
        except Exception as ex:
            emit('error', message=str(ex))
        while self.running:
            try:
                try:
                    cmd = self.commands.get(timeout=.02 if self.cap is None else .001)
                    self.handle(cmd)
                except queue.Empty:
                    pass
                if self.cap is not None and self.running:
                    self.frame()
            except Exception as ex:
                traceback.print_exc()
                if self.calibration:
                    self.calibration = False
                    self.collection = None
                    emit('calibration_failed', message=str(ex))
                else:
                    self.close_camera()
                    emit('stopped')
                    emit('error', message=str(ex))
        self.close_camera()


if __name__ == '__main__':
    Engine().run()
