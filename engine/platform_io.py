"""Small operating-system boundary for the standalone gaze pointer."""
from __future__ import annotations

import sys
import time
from pathlib import Path


def _mac_camera_devices():
    import AVFoundation as AV

    devices = AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo)
    result = []
    for index, device in enumerate(devices):
        kind = str(device.deviceType()).replace("AVCaptureDeviceType", "")
        result.append({
            "index": index,
            "name": str(device.localizedName()),
            "id": str(device.uniqueID()),
            "kind": kind,
            "builtin": kind == "BuiltInWideAngleCamera",
            "phone": kind in ("ContinuityCamera", "DeskViewCamera"),
        })
    return result


def _opencv_camera_devices(cv2_module, platform):
    if platform == "win32":
        backends = [getattr(cv2_module, "CAP_DSHOW", 0),
                    getattr(cv2_module, "CAP_MSMF", 0)]
    else:
        backends = [getattr(cv2_module, "CAP_ANY", 0)]
    result = []
    for index in range(10):
        for backend in dict.fromkeys(backends):
            capture = cv2_module.VideoCapture(index, backend)
            try:
                if capture.isOpened():
                    result.append({"index": index, "name": f"Camera {index}",
                                   "id": f"opencv:{index}", "kind": "External",
                                   "builtin": index == 0, "phone": False})
                    break
            finally:
                capture.release()
    return result


def camera_devices(cv2_module=None, platform=None):
    """List cameras without opening them on macOS; probe OpenCV on other OSes."""
    platform = platform or sys.platform
    if cv2_module is None:
        import cv2 as cv2_module
    if platform == "darwin":
        try:
            return _mac_camera_devices()
        except (ImportError, AttributeError, RuntimeError):
            return _opencv_camera_devices(cv2_module, platform)
    return _opencv_camera_devices(cv2_module, platform)


def camera_backend(cv2_module=None, platform=None):
    platform = platform or sys.platform
    if cv2_module is None:
        import cv2 as cv2_module
    if platform == "darwin":
        return getattr(cv2_module, "CAP_AVFOUNDATION", getattr(cv2_module, "CAP_ANY", 0))
    if platform == "win32":
        return getattr(cv2_module, "CAP_DSHOW", getattr(cv2_module, "CAP_ANY", 0))
    return getattr(cv2_module, "CAP_ANY", 0)


def ensure_camera_access(timeout=8.0):
    """macOS에서 이 프로세스의 카메라 권한이 확정될 때까지 기다립니다.

    앱이 권한을 받아도 엔진 자식 프로세스는 notDetermined 상태로 시작합니다. 그대로 열면
    OpenCV가 비동기 요청만 걸어 두고 즉시 실패하므로, 여기서 먼저 요청하고 결과를 기다립니다.
    반환값은 접근이 허용되었는지 여부이며, macOS가 아니면 항상 True입니다.
    """
    if sys.platform != "darwin":
        return True
    try:
        import AVFoundation as AV
    except ImportError as ex:
        print(f"camera-access: AVFoundation 임포트 실패 {ex}", file=sys.stderr, flush=True)
        return True
    import threading

    authorized, not_determined = 3, 0
    status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo)
    print(f"camera-access: authorizationStatus={status}", file=sys.stderr, flush=True)
    if status == authorized:
        return True
    if status != not_determined:
        return False
    done = threading.Event()
    result = {"granted": False}

    def completion(granted):
        result["granted"] = bool(granted)
        done.set()

    AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeVideo, completion)
    done.wait(timeout)
    if done.is_set():
        return result["granted"]
    # 콜백이 늦는 경우가 있어 상태를 한 번 더 확인합니다.
    return AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo) == authorized


def open_camera(index, width=1280, height=720, fps=30, cv2_module=None, platform=None):
    """Open a camera using the native capture backend for the host OS."""
    platform = platform or sys.platform
    if cv2_module is None:
        import cv2 as cv2_module
    preferred = camera_backend(cv2_module, platform)
    backends = [preferred]
    if platform == "win32":
        backends.extend([getattr(cv2_module, "CAP_MSMF", preferred),
                         getattr(cv2_module, "CAP_ANY", preferred)])
    elif platform == "darwin":
        backends.append(getattr(cv2_module, "CAP_ANY", preferred))
    if platform == "darwin" and not ensure_camera_access():
        raise RuntimeError("카메라 권한이 없습니다. 시스템 설정 → 개인정보 보호 및 보안 → 카메라에서 SeeOSK를 허용해 주세요.")
    capture = None
    # 권한이 막 확정된 직후에는 첫 열기가 실패할 수 있어 짧게 재시도합니다.
    for attempt in range(3):
        for backend in dict.fromkeys(backends):
            candidate = cv2_module.VideoCapture(index, backend)
            if candidate.isOpened():
                capture = candidate
                break
            candidate.release()
        if capture is not None:
            break
        if attempt < 2:
            print(f"camera-access: 열기 재시도 {attempt + 1}", file=sys.stderr, flush=True)
            time.sleep(0.6)
    if capture is None:
        raise RuntimeError(f"카메라 {index}를 열지 못했습니다.")
    capture.set(cv2_module.CAP_PROP_FRAME_WIDTH, width)
    capture.set(cv2_module.CAP_PROP_FRAME_HEIGHT, height)
    capture.set(cv2_module.CAP_PROP_FPS, fps)
    return capture


def screen_size(platform=None):
    """Return primary-display dimensions in the pointer API's coordinate space."""
    platform = platform or sys.platform
    if platform == "darwin":
        from Quartz import CGDisplayBounds, CGMainDisplayID
        bounds = CGDisplayBounds(CGMainDisplayID())
        size = float(bounds.size.width), float(bounds.size.height)
        if size[0] > 0 and size[1] > 0:
            return size
        raise RuntimeError("화면 크기를 읽지 못했습니다. 로그인된 macOS 데스크톱에서 실행해 주세요.")
    if platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
        user32 = ctypes.windll.user32
        size = float(user32.GetSystemMetrics(0)), float(user32.GetSystemMetrics(1))
        if size[0] > 0 and size[1] > 0:
            return size
        raise RuntimeError("화면 크기를 읽지 못했습니다. Windows 데스크톱에서 실행해 주세요.")
    raise RuntimeError(f"지원하지 않는 데스크톱 운영체제입니다: {platform}")


def user_data_dir(app_name="SeeOSK", platform=None, home=None, environ=None):
    """Per-user writable location for calibration profiles and logs."""
    import os
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    if platform == "darwin":
        return home / "Library" / "Application Support" / app_name
    if platform == "win32":
        root = environ.get("APPDATA") or environ.get("LOCALAPPDATA") or str(home / "AppData" / "Roaming")
        return Path(root) / app_name
    return Path(environ.get("XDG_DATA_HOME", home / ".local" / "share")) / app_name
