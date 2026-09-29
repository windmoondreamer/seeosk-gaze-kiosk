"""ONNX 클릭 영역 탐지기. torch/ultralytics 없이 onnxruntime만 사용합니다."""
from __future__ import annotations

import cv2
import numpy as np
import onnxruntime as ort

PAD = 114


class OnnxDetector:
    """NMS를 내장해 내보낸 YOLO26 detect 모델(출력 1x300x6)을 실행합니다."""

    def __init__(self, model_path, size=640):
        self.size = size
        self.session = ort.InferenceSession(str(model_path), providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name
        self.names = {0: 'clickable'}

    def _letterbox(self, frame):
        h, w = frame.shape[:2]
        scale = min(self.size / h, self.size / w)
        nh, nw = round(h * scale), round(w * scale)
        # ultralytics LetterBox와 같이 확대·축소 모두 INTER_LINEAR을 씁니다. 다른 보간을 쓰면 탐지 결과가 어긋납니다.
        resized = frame if (nh, nw) == (h, w) else cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        top, left = (self.size - nh) // 2, (self.size - nw) // 2
        canvas = np.full((self.size, self.size, 3), PAD, dtype=np.uint8)
        canvas[top:top + nh, left:left + nw] = resized
        return canvas, scale, left, top

    def detect(self, frame, conf=0.35):
        """BGR 프레임에서 [x1, y1, x2, y2, confidence, class_id]를 원본 좌표로 돌려줍니다."""
        canvas, scale, left, top = self._letterbox(frame)
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        output = self.session.run(None, {self.input_name: np.ascontiguousarray(blob)})[0][0]
        h, w = frame.shape[:2]
        results = []
        for x1, y1, x2, y2, score, cls in output:
            if score < conf:
                continue
            box = [float(np.clip((x1 - left) / scale, 0, w)), float(np.clip((y1 - top) / scale, 0, h)),
                   float(np.clip((x2 - left) / scale, 0, w)), float(np.clip((y2 - top) / scale, 0, h))]
            results.append(box + [float(score), int(cls)])
        return results
