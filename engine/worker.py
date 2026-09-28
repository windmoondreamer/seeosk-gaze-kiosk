#!/usr/bin/env python3
"""Local JSON-lines worker. Camera frames never leave the process except preview."""
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
                  head_ok, stable_median, save_profile, load_profile)
from legacy_gaze import FaceTracker, extract_features, OneEuro
if __name__ == '__main__':
    faulthandler.cancel_dump_traceback_later()
    print('Camera libraries loaded.', flush=True)

ROOT = Path(__file__).resolve().parent.parent
DATA = Path.home() / 'Library/Application Support/SeeOSK'
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
    import AVFoundation as AV
    # OpenCV's AVFoundation backend uses this same ordered device list.
    out = []
    for i, d in enumerate(AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo)):
        kind = str(d.deviceType())
        out.append(dict(index=i, name=str(d.localizedName()), id=str(d.uniqueID()),
                        phone=('Continuity' in kind or 'DeskView' in kind),
                        builtin='BuiltIn' in kind))
    return out


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
                    import torch
                    torch.set_num_threads(2)
                    from ultralytics import YOLO
                    model = YOLO(str(ROOT/'assets/best.pt'))
                frame = cv2.imread(job['path'])
                if frame is None:
                    raise ValueError('화면 이미지를 읽지 못했습니다.')
                h, w = frame.shape[:2]
                t = time.monotonic()
                result = model.predict(frame, imgsz=640, conf=.35, device='cpu', verbose=False)[0]
                regions = []
                for box in result.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    if x2-x1 < 8 or y2-y1 < 8:
                        continue
                    regions.append(dict(x=x1/w, y=y1/h, w=(x2-x1)/w, h=(y2-y1)/h,
                                        confidence=float(box.conf[0]), label=result.names[int(box.cls[0])]))
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
        self.commands = queue.Queue()
        self.cap = None
        self.tracker = None
        self.camera = None
        self.geometry = None
        self.profile = None
        self.backup = None
        self.candidate = None
        self.calibration = False
        self.samples, self.targets, self.checks, self.check_targets = [], [], [], []
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
        self.reset_filters()

    def handle(self, c):
        cmd = c.get('cmd')
        if cmd == 'shutdown':
            self.running = False
        elif cmd == 'configure':
            geometry = c['geometry']
            if geometry != self.geometry:
                self.geometry = geometry
                self.profile = None
                self.collection = None
                self.calibration = False
                self.reset_filters()
                emit('calibration_invalid', message='창 위치·크기에 맞춰 캘리브레이션해 주세요.')
                if self.camera:
                    self.profile = load_profile(self.profile_path(), self.camera['id'], geometry)
                    if self.profile:
                        emit('profile', validation=self.profile['validation'], restored=True)
        elif cmd == 'start':
            self.close_camera()
            found = devices()
            emit('cameras', devices=found)
            self.camera = next((d for d in found if d['index'] == int(c['camera'])), None)
            if int(c['camera']) < 0:
                self.camera = next((d for d in found if d['builtin']), next((d for d in found if not d['phone']), None))
            if self.camera is None:
                raise ValueError('선택한 카메라가 연결되어 있지 않습니다.')
            self.cap = cv2.VideoCapture(self.camera['index'], cv2.CAP_AVFOUNDATION)
            if not self.cap.isOpened():
                self.close_camera()
                raise ValueError('카메라를 열 수 없습니다. 시스템 설정의 카메라 권한을 확인해 주세요.')
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            self.tracker = FaceTracker()
            self.failures = 0
            self.profile = load_profile(self.profile_path(), self.camera['id'], self.geometry)
            emit('started', camera=self.camera['name'])
            if self.profile:
                emit('profile', validation=self.profile['validation'], restored=True)
        elif cmd == 'stop':
            self.close_camera()
            emit('stopped')
        elif cmd == 'calibrate':
            if self.cap is None or not self.geometry:
                raise ValueError('카메라를 먼저 시작해 주세요.')
            self.generation = int(c['generation'])
            self.backup, self.profile = self.profile, None
            self.calibration = True
            self.candidate = None
            self.samples, self.targets, self.checks, self.check_targets = [], [], [], []
            self.reference = None
            self.collection = None
            self.reset_filters()
            emit('calibration_started', generation=self.generation)
        elif cmd == 'collect':
            if not self.calibration or c['generation'] != self.generation:
                return
            checking = c.get('phase') == 'check'
            index = len(self.checks) if checking else len(self.samples)
            points = CHECK_POINTS if checking else CAL_POINTS
            if c['index'] != index or index >= len(points) or (checking and self.candidate is None):
                raise ValueError('보정 순서가 맞지 않습니다. 다시 시작해 주세요.')
            w, h = self.geometry['width'], self.geometry['height']
            self.collection = dict(checking=checking, index=index, target=[points[index][0]*w, points[index][1]*h],
                                   samples=[], heads=[], elapsed=0., begun=time.monotonic(), last_valid=None)
        elif cmd == 'cancel':
            self.profile = self.backup
            self.backup = None
            self.collection = None
            self.calibration = False
            self.reset_filters()
            emit('cancelled', calibrated=self.profile is not None)
        elif cmd == 'detect':
            self.detector.submit(c)
        elif cmd == 'list_cameras':
            emit('cameras', devices=devices())

    def sample(self, iris, head, now, valid):
        c = self.collection
        if c is None:
            return
        if now-c['begun'] > 25:
            self.collection = None
            emit('sample_retry', generation=self.generation, message='얼굴·조명·자세를 확인한 후 같은 점을 다시 시도합니다.')
            return
        if not valid:
            c['last_valid'] = None
            emit('calibration_progress', progress=c['elapsed']/.65, hint='눈을 뜨고 머리를 편안하게 고정해 주세요.')
            return
        if c['last_valid'] is not None:
            c['elapsed'] += min(.1, now-c['last_valid'])
        c['last_valid'] = now
        c['samples'].append(iris)
        c['heads'].append(head)
        emit('calibration_progress', progress=min(1., c['elapsed']/.65), hint='점을 계속 바라보세요.')
        if c['elapsed'] < .65 or len(c['samples']) < 12:
            return
        try:
            point = stable_median(c['samples'])
        except ValueError as ex:
            self.collection = None
            emit('sample_retry', generation=self.generation, message=str(ex))
            return
        self.collection = None
        if self.reference is None:
            self.reference = np.median(c['heads'], axis=0).tolist()
        if c['checking']:
            self.checks.append(point)
            self.check_targets.append(c['target'])
            if len(self.checks) == len(CHECK_POINTS):
                metrics = validate(self.candidate, self.checks, self.check_targets,
                                   [self.geometry['width'], self.geometry['height']])
                self.calibration = False
                # Validation measures accuracy; a poor score must not silently disable the cursor.
                metrics['pointer_usable'] = True
                self.profile = dict(self.candidate, schema=1, camera_id=self.camera['id'],
                                    geometry=self.geometry, head_reference=self.reference,
                                    validation=metrics, created=time.strftime('%Y-%m-%d %H:%M:%S'))
                save_profile(self.profile_path(), self.profile)
                print('Calibration result:', metrics, file=sys.stderr, flush=True)
                self.backup = None
                emit('calibration_result', validation=metrics, generation=self.generation)
                return
        else:
            self.samples.append(point)
            self.targets.append(c['target'])
            if len(self.samples) == len(CAL_POINTS):
                self.candidate = fit(self.samples, self.targets)
        emit('sample_done', phase='check' if c['checking'] else 'calibrate', index=c['index'], generation=self.generation)

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
        features = extract_features(lm, w, h) if lm is not None else None
        valid = bool(features and features['has_iris'] and features['face_w'] >= 90)
        reason = '얼굴을 카메라 정면에 두세요.'
        iris, head = None, None
        if valid:
            iris = [features['gaze_x'], features['gaze_y']]
            head = head_vector(features, w).tolist()
            if max(blend.get('eyeBlinkLeft', 0), blend.get('eyeBlinkRight', 0)) > .50:
                valid, reason = False, '눈 깜빡임 감지'
            reference = self.reference if self.calibration else (self.profile or {}).get('head_reference')
            if reference is not None and not head_ok(head, reference):
                valid, reason = False, '보정할 때의 머리 위치로 돌아오거나 다시 보정해 주세요.'
        self.sample(iris, head, now, valid)
        dt = max(.001, now-self.last_frame)
        self.last_frame = now
        data = dict(valid=valid, calibrated=self.profile is not None, fps=round(min(60., 1/dt), 1),
                    reason='얼굴·눈동자 감지 중' if valid else reason)
        if valid and self.profile is not None and not self.calibration:
            xy = predict(self.profile, iris)
            gw, gh = self.geometry['width'], self.geometry['height']
            if not np.isfinite(xy).all():
                data.update(valid=False, reason='시선이 보정 범위를 벗어났습니다.')
                self.reset_filters()
            else:
                # Clamp before filtering so an offscreen estimate cannot freeze the cursor
                # or leave the filter carrying a large overshoot.
                xy = np.clip(xy, [0., 0.], [gw-1., gh-1.])
                xy = [self.filters[i](float(xy[i]), dt) for i in range(2)]
                data.update(x=max(0., min(gw, xy[0])), y=max(0., min(gh, xy[1])))
        if not valid:
            self.reset_filters()
        if now-self.last_preview > .25:
            preview = cv2.resize(frame, (320, int(h*320/w)))
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
