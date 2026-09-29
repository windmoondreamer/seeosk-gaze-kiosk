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
from legacy_gaze import FaceTracker, extract_features, OneEuro, ThresholdDetector
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


class Engine:
    def __init__(self):
        self.head_pointer = HeadPointer()
        self.mouth = ThresholdDetector('jawopen')
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
            self.mouth.recalibrate()
            self.failures = 0
            emit('started', camera=self.camera['name'])
        elif cmd == 'stop':
            self.close_camera()
            emit('stopped')
        elif cmd == 'recenter':
            self.head_pointer.reset()
            self.mouth.recalibrate()
            emit('centering')
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
            fired = self.mouth.update(now, {'blend': blend})
            data.update(mouth=round(self.mouth.value, 3), mouth_state=self.mouth.state)
            if fired is not None and data['valid']:
                emit('gesture_click', gesture='jawopen', x=data['x'], y=data['y'])
        else:
            self.mouth.arm_t = None
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
