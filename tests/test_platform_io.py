import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import platform_io
import legacy_gaze


class FakeCapture:
    def __init__(self, opened):
        self.opened = opened
        self.released = False
        self.properties = {}

    def isOpened(self):
        return self.opened

    def release(self):
        self.released = True

    def set(self, key, value):
        self.properties[key] = value


class FakeCV2:
    CAP_ANY = 0
    CAP_DSHOW = 700
    CAP_MSMF = 1400
    CAP_AVFOUNDATION = 1200
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5

    def __init__(self, open_when):
        self.open_when = open_when
        self.calls = []
        self.captures = []

    def VideoCapture(self, index, backend):
        self.calls.append((index, backend))
        cap = FakeCapture(self.open_when(index, backend))
        self.captures.append(cap)
        return cap


class PlatformIOTests(unittest.TestCase):
    def test_macos_camera_discovery_does_not_probe_or_wake_devices(self):
        cv2 = FakeCV2(lambda _index, _backend: True)
        expected = [{"index": 0, "name": "Mac Camera", "id": "mac-camera",
                     "kind": "BuiltInWideAngleCamera", "builtin": True, "phone": False}]
        with patch.object(platform_io, "_mac_camera_devices", return_value=expected):
            self.assertEqual(platform_io.camera_devices(cv2, "darwin"), expected)
        self.assertEqual(cv2.calls, [])

    def test_windows_camera_discovery_uses_opencv_and_releases_probe_handles(self):
        cv2 = FakeCV2(lambda index, backend: index == 2 and backend == FakeCV2.CAP_DSHOW)
        result = platform_io.camera_devices(cv2, "win32")
        self.assertEqual([d["index"] for d in result], [2])
        self.assertTrue(all(c.released for c in cv2.captures))

    def test_windows_capture_falls_back_from_directshow_to_media_foundation(self):
        cv2 = FakeCV2(lambda _index, backend: backend == FakeCV2.CAP_MSMF)
        cap = platform_io.open_camera(1, 1280, 720, 30, cv2, "win32")
        self.assertEqual(cv2.calls, [(1, FakeCV2.CAP_DSHOW), (1, FakeCV2.CAP_MSMF)])
        self.assertTrue(cv2.captures[0].released)
        self.assertEqual(cap.properties, {3: 1280, 4: 720, 5: 30})

    def test_macos_waits_for_authorization_before_opening_the_camera(self):
        # 엔진 자식 프로세스는 notDetermined로 시작합니다. 권한 확정 전에 열면 OpenCV가 그냥 실패합니다.
        cv2 = FakeCV2(lambda _index, _backend: True)
        order = []
        with patch.object(platform_io, "ensure_camera_access",
                          side_effect=lambda *a, **k: order.append("access") or True):
            original = cv2.VideoCapture
            cv2.VideoCapture = lambda i, b: (order.append("open"), original(i, b))[1]
            platform_io.open_camera(0, 1280, 720, 30, cv2, "darwin")
        self.assertEqual(order[0], "access")

    def test_macos_refuses_to_open_when_access_is_denied(self):
        cv2 = FakeCV2(lambda _index, _backend: True)
        with patch.object(platform_io, "ensure_camera_access", return_value=False):
            with self.assertRaises(RuntimeError) as caught:
                platform_io.open_camera(0, 1280, 720, 30, cv2, "darwin")
        self.assertIn("카메라 권한", str(caught.exception))
        self.assertEqual(cv2.calls, [])

    def test_first_open_failure_after_authorization_is_retried(self):
        # 권한이 막 확정된 직후 첫 열기가 실패하던 실제 증상을 재현합니다.
        attempts = {"n": 0}

        def open_when(_index, _backend):
            attempts["n"] += 1
            return attempts["n"] > 2

        cv2 = FakeCV2(open_when)
        with patch.object(platform_io, "ensure_camera_access", return_value=True), \
             patch.object(platform_io.time, "sleep") as sleep:
            capture = platform_io.open_camera(0, 1280, 720, 30, cv2, "darwin")
        self.assertTrue(capture.isOpened())
        self.assertGreater(attempts["n"], 2)
        self.assertTrue(sleep.called)

    def test_retries_give_up_and_report_the_camera_index(self):
        cv2 = FakeCV2(lambda _index, _backend: False)
        with patch.object(platform_io, "ensure_camera_access", return_value=True), \
             patch.object(platform_io.time, "sleep"):
            with self.assertRaises(RuntimeError) as caught:
                platform_io.open_camera(3, 1280, 720, 30, cv2, "darwin")
        self.assertIn("카메라 3", str(caught.exception))
        self.assertTrue(all(cap.released for cap in cv2.captures))

    def test_user_data_uses_native_application_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(platform_io.user_data_dir(home=home, platform="darwin"),
                             home / "Library/Application Support/SeeOSK")
            self.assertEqual(platform_io.user_data_dir(home=home, platform="win32",
                                                      environ={"APPDATA": "C:/Users/test/AppData/Roaming"}),
                             Path("C:/Users/test/AppData/Roaming/SeeOSK"))

    def test_profile_directory_is_created_under_user_data_when_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_root = Path(tmp) / "AppData" / "SeeOSK" / "profiles"
            with patch.object(legacy_gaze, "PROFILE_DIR", profile_root):
                path = legacy_gaze.profile_path("Camera 0", "head")
            self.assertTrue(profile_root.is_dir())
            self.assertEqual(path.parent, profile_root)

    def test_unsupported_screen_backend_fails_with_platform_name(self):
        with self.assertRaisesRegex(RuntimeError, "linux"):
            platform_io.screen_size("linux")

    def test_empty_display_geometry_is_reported_instead_of_starting_calibration_at_zero(self):
        quartz = types.ModuleType("Quartz")
        quartz.CGMainDisplayID = lambda: 1
        quartz.CGDisplayBounds = lambda _display: types.SimpleNamespace(
            size=types.SimpleNamespace(width=0, height=0))
        with patch.dict(sys.modules, {"Quartz": quartz}):
            with self.assertRaisesRegex(RuntimeError, "로그인된 macOS 데스크톱"):
                platform_io.screen_size("darwin")


if __name__ == "__main__":
    unittest.main()
