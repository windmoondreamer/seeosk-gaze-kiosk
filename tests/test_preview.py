import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from preview import blur_background, landmark_bounds


def face_frame():
    """왼쪽 위에 얼굴, 오른쪽 아래에 배경 무늬가 있는 합성 프레임."""
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    frame[:, ::2] = 255            # 세로 줄무늬. 흐려지면 대비가 줄어듭니다.
    return frame


def marks(x0, y0, x1, y1):
    return [SimpleNamespace(x=x0, y=y0), SimpleNamespace(x=x1, y=y0),
            SimpleNamespace(x=x0, y=y1), SimpleNamespace(x=x1, y=y1)]


def contrast(region):
    return float(region.astype(np.float32).std())


class LandmarkBoundsTests(unittest.TestCase):
    def test_bounds_come_from_the_extremes(self):
        self.assertEqual(landmark_bounds(marks(.2, .3, .6, .7)), (.2, .3, .6, .7))

    def test_tuple_landmarks_are_accepted(self):
        self.assertEqual(landmark_bounds([(.1, .2), (.4, .5)]), (.1, .2, .4, .5))

    def test_empty_or_broken_input_has_no_bounds(self):
        self.assertIsNone(landmark_bounds(None))
        self.assertIsNone(landmark_bounds([]))
        self.assertIsNone(landmark_bounds([SimpleNamespace(x=float("nan"), y=.5)]))
        self.assertIsNone(landmark_bounds(object()))   # 순회할 수 없는 값


class BlurBackgroundTests(unittest.TestCase):
    def test_face_stays_sharp_and_background_is_blurred(self):
        frame = face_frame()
        # 얼굴을 왼쪽 위 사분면에 둡니다.
        out = blur_background(frame, marks(.08, .10, .32, .55))
        face = (slice(20, 80), slice(30, 90))
        background = (slice(120, 170), slice(230, 310))
        self.assertGreater(contrast(out[face]), contrast(out[background]) * 3)
        self.assertLess(contrast(out[background]), contrast(frame[background]) / 3)

    def test_a_missing_face_blurs_the_whole_frame(self):
        frame = face_frame()
        out = blur_background(frame, None)
        self.assertLess(contrast(out), contrast(frame) / 3)

    def test_the_original_frame_is_not_modified(self):
        frame = face_frame()
        before = frame.copy()
        blur_background(frame, marks(.1, .1, .4, .5))
        self.assertTrue(np.array_equal(frame, before))

    def test_shape_and_dtype_are_preserved(self):
        frame = face_frame()
        out = blur_background(frame, marks(.1, .1, .4, .5))
        self.assertEqual(out.shape, frame.shape)
        self.assertEqual(out.dtype, frame.dtype)

    def test_an_empty_frame_is_returned_untouched(self):
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        self.assertEqual(blur_background(empty, None).shape, empty.shape)
        self.assertIsNone(blur_background(None, None))


if __name__ == "__main__":
    unittest.main()
