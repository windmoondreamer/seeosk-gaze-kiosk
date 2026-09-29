# Standalone SeeOSK for macOS and Windows

## Overview
Provide a double-clickable standalone launcher for the existing OpenCV gaze/head pointer, with shared calibration and tracking logic and explicit OS adapters. The macOS Accessibility Head Pointer settings route remains an optional macOS-only path in the native SeeOSK app; the standalone tracker uses its own MediaPipe estimate and cursor driver.

## Architecture decisions
- Reuse `engine/legacy_gaze.py` as the standalone desktop flow: it already owns calibration, gaze/head models, OpenCV preview, and pointer gestures.
- Move camera enumeration/opening and display-size queries into an OS adapter. Keep MediaPipe feature extraction and mapping platform-neutral.
- Use pynput for OS cursor movement in the standalone flow (macOS Accessibility permission required; Windows uses its native input backend).
- Keep the existing Swift/WebKit app macOS-only; do not claim it runs on Windows. The standalone launchers run the shared Python program on both platforms.
- Pin a minimal standalone dependency set without the kiosk detector; let pynput select its native OS backend.

## Tasks
1. Add a shared platform adapter for macOS/Windows camera enumeration, camera opening, and screen geometry; cover behavior with mocked tests.
2. Route the existing worker and standalone gaze runner through that adapter; retain the macOS built-in-camera preference.
3. Add double-click launch/install entry points and a minimal cross-platform requirements file; explain permissions and platform scope.
4. Run the Python suite, syntax checks, and a build/package smoke check available on this Mac.

## Risks and limits
- Windows cannot be run on this Mac; Windows camera permissions, DPI scaling, pynput cursor movement, and packaging need a Windows-host smoke test.
- MediaPipe 0.10.21 needs a supported Python release; launchers will require Python 3.12.
- The legacy OpenCV UI is a standalone window, not the full SeeOSK kiosk-analysis dashboard.
