# Standalone macOS + Windows checklist

- [x] Add OS adapter for camera listing/opening and screen dimensions.
- [x] Replace direct AVFoundation/Quartz references in shared Python paths.
- [x] Add platform adapter tests with mocked camera/display APIs.
- [x] Add standalone dependency pins and double-click launchers.
- [x] Document macOS cursor permission, Windows camera permission, and Windows-host verification gap.
- [x] Run Python tests, syntax checks, and macOS bundle build/signature verification.
- [ ] Run Windows camera, DPI, cursor, and click smoke tests on a Windows host.

# 배포 패키지 (2026-09-29)

- [x] 낡은 macOS·통합·소스 ZIP 3개를 현재 앱 기준으로 재생성. Windows ZIP은 폴더와 해시 일치해 유지.
- [x] 번들에서 torch·ultralytics·jax·polars·scipy 제외 (2.0GB → 529MB).
- [x] 클릭 영역 탐지를 onnxruntime으로 옮기고 torch 경로와 800장 대조.
- [x] `packaging/build_windows.py` launcher 튜플 버그 수정.
- [x] `release-notes.md`·`실행안내.txt`를 현재 앱 동작에 맞게 갱신.
- [ ] 배포/macOS의 SeeOSK.previous-20260929-105449.app(87MB) 삭제 — 권한 거부로 미수행.
- [ ] 변경 사항 git 커밋.
