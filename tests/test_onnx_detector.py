import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
import onnx_detector
from onnx_detector import OnnxDetector

MODEL = ROOT / "assets/best.onnx"
SAMPLE = ROOT / "web/samples/screen_000001.png"


class FakeSession:
    """고정 박스 하나를 돌려주는 onnxruntime 세션 대역."""

    def __init__(self, box):
        self.box = box
        self.last_blob = None

    def get_inputs(self):
        return [type("I", (), {"name": "images"})()]

    def run(self, _outputs, feed):
        self.last_blob = feed["images"]
        row = np.array(self.box, dtype=np.float32)
        padding = np.zeros((299, 6), dtype=np.float32)
        return [np.concatenate([row[None], padding])[None]]


def build(box):
    session = FakeSession(box)
    with patch.object(onnx_detector.ort, "InferenceSession", return_value=session):
        return OnnxDetector("unused.onnx"), session


class GeometryTests(unittest.TestCase):
    def test_letterbox_padding_is_centered_and_neutral(self):
        detector, _ = build([0, 0, 0, 0, 0, 0])
        canvas, scale, left, top = detector._letterbox(np.zeros((360, 640, 3), dtype=np.uint8))
        self.assertEqual(canvas.shape, (640, 640, 3))
        self.assertAlmostEqual(scale, 1.0)
        self.assertEqual((left, top), (0, 140))
        self.assertTrue((canvas[0] == onnx_detector.PAD).all())
        self.assertTrue((canvas[320] == 0).all())

    def test_boxes_map_back_to_original_pixels(self):
        # 640x360 입력은 배율 1.0, 위아래 140px 패딩으로 들어갑니다.
        detector, _ = build([100, 200, 300, 400, 0.9, 0])
        (x1, y1, x2, y2, score, cls), = detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
        self.assertEqual((x1, y1, x2, y2), (100.0, 60.0, 300.0, 260.0))
        self.assertAlmostEqual(score, 0.9, places=5)
        self.assertEqual(cls, 0)

    def test_boxes_are_clipped_to_the_frame(self):
        detector, _ = build([-50, 0, 700, 640, 0.9, 0])
        (x1, y1, x2, y2, _, _), = detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
        self.assertEqual((x1, y1, x2, y2), (0.0, 0.0, 640.0, 360.0))

    def test_low_confidence_rows_are_dropped(self):
        detector, _ = build([10, 10, 20, 20, 0.2, 0])
        self.assertEqual(detector.detect(np.zeros((360, 640, 3), dtype=np.uint8), conf=0.35), [])

    def test_blob_is_rgb_normalized_nchw(self):
        detector, session = build([0, 0, 0, 0, 0, 0])
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[:, :, 0] = 255  # BGR의 파랑 채널
        detector.detect(frame)
        blob = session.last_blob
        self.assertEqual(blob.shape, (1, 3, 640, 640))
        self.assertEqual(blob.dtype, np.float32)
        # RGB로 뒤집혔으므로 파랑은 마지막 채널에 1.0으로 들어갑니다.
        self.assertAlmostEqual(float(blob[0, 2, 320, 320]), 1.0, places=5)
        self.assertAlmostEqual(float(blob[0, 0, 320, 320]), 0.0, places=5)


@unittest.skipUnless(MODEL.exists() and SAMPLE.exists(), "내보낸 모델 또는 학습 화면 없음")
class RealModelTests(unittest.TestCase):
    def test_first_training_screen_keeps_its_sixteen_regions(self):
        import cv2

        boxes = OnnxDetector(MODEL).detect(cv2.imread(str(SAMPLE)), conf=0.35)
        # torch 경로와 대조해 확인한 값입니다(최저 IoU 0.980).
        self.assertEqual(len(boxes), 16)
        h, w = cv2.imread(str(SAMPLE)).shape[:2]
        for x1, y1, x2, y2, score, cls in boxes:
            self.assertTrue(0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h)
            self.assertGreaterEqual(score, 0.35)
            self.assertEqual(cls, 0)


if __name__ == "__main__":
    unittest.main()
