#!/usr/bin/env python3
"""Desktop entry point, with a packaged-runtime check and visible startup errors."""
import sys
import traceback
from pathlib import Path


def main():
    import legacy_gaze
    if "--self-test" in sys.argv:
        from pynput.mouse import Controller, Button
        from pynput.keyboard import Controller as KeyboardController
        assert legacy_gaze.MODEL_PATH.is_file(), "Face model is missing"
        if sys.platform == "darwin":
            import AVFoundation
            import Quartz
        print("SeeOSK runtime OK: pointer backends, vision imports, face model")
        return
    if sys.platform == "darwin" and getattr(sys, "frozen", False) and not any(
        flag in sys.argv for flag in ("--help", "-h", "--list", "--compare")
    ):
        import AVFoundation as AV
        import threading
        status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo)
        if status == AV.AVAuthorizationStatusNotDetermined:
            done = threading.Event()
            AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AV.AVMediaTypeVideo, lambda granted: done.set()
            )
            if not done.wait(90):
                raise RuntimeError("카메라 권한 응답을 기다리고 있습니다. 권한 알림에서 허용한 뒤 다시 실행하세요.")
            status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo)
        if status != AV.AVAuthorizationStatusAuthorized:
            raise RuntimeError("카메라 권한이 허용되지 않았습니다. 시스템 설정 → 개인정보 보호 및 보안 → 카메라에서 SeeOSK를 허용한 뒤 다시 실행하세요.")
    legacy_gaze.main()


if __name__ == "__main__":
    try:
        main()
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code in (None, 0):
            raise
        if not getattr(sys, "frozen", False):
            raise
        from platform_io import user_data_dir
        log = user_data_dir() / "startup-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        if sys.platform == "darwin":
            from AppKit import NSAlert, NSApplication
            NSApplication.sharedApplication()
            alert = NSAlert.alloc().init()
            alert.setMessageText_("SeeOSK 실행을 시작하지 못했습니다")
            alert.setInformativeText_(str(error) + "\n\n오류 기록: " + str(log))
            alert.addButtonWithTitle_("닫기")
            alert.addButtonWithTitle_("카메라 설정 열기")
            response = alert.runModal()
            if response == 1001:
                from AppKit import NSWorkspace
                from Foundation import NSURL
                NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
                ))
        raise SystemExit(1)
