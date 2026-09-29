import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import legacy_gaze


def args():
    return SimpleNamespace(camera=None, width=1280, height=720, no_flip=False,
                           pointer="head", calibrate=False, fix_head=False)


class NoCameraStartupTests(unittest.TestCase):
    """카메라가 없는 PC에서도 배포본이 창을 띄우고 정상 종료해야 합니다."""

    def test_missing_device_shows_a_window_instead_of_crashing(self):
        with patch.object(legacy_gaze, "avf_devices", return_value=[]), \
             patch.object(legacy_gaze, "show_no_camera_window") as window, \
             patch.object(legacy_gaze, "Camera") as camera:
            legacy_gaze.run(args())
        window.assert_called_once()
        camera.assert_not_called()

    def test_camera_that_will_not_open_shows_a_window_instead_of_crashing(self):
        devices = [{"index": 0, "name": "Cam", "builtin": True, "phone": False}]
        with patch.object(legacy_gaze, "avf_devices", return_value=devices), \
             patch.object(legacy_gaze, "screen_size", return_value=(1920, 1080)), \
             patch.object(legacy_gaze, "show_no_camera_window") as window, \
             patch.object(legacy_gaze, "Camera", side_effect=RuntimeError("카메라 0를 열지 못했습니다.")):
            legacy_gaze.run(args())
        window.assert_called_once()
        self.assertIn("0", window.call_args.args[0])

    def test_the_notice_window_survives_a_headless_display(self):
        import cv2
        with patch.object(cv2, "namedWindow", side_effect=cv2.error("no display")), \
             patch.object(cv2, "destroyAllWindows") as destroy:
            legacy_gaze.show_no_camera_window("test", seconds=0)
        destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
