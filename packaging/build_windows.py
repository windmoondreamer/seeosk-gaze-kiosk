#!/usr/bin/env python3
"""Build the self-contained Windows x64 folder from macOS or Windows."""
from __future__ import annotations

import shutil
import subprocess
import urllib.request
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent.parent / "배포/Windows/SeeOSK_Windows"
BUILD = ROOT / "build/windows-package"
PYTHON_ZIP = BUILD / "python-3.12.10-embed-amd64.zip"
PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
SITE = OUT / "runtime/Lib/site-packages"


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    if not PYTHON_ZIP.exists():
        urllib.request.urlretrieve(PYTHON_URL, PYTHON_ZIP)

    if OUT.exists():
        shutil.rmtree(OUT)
    runtime = OUT / "runtime"
    runtime.mkdir(parents=True)
    with ZipFile(PYTHON_ZIP) as package:
        package.extractall(runtime)

    subprocess.run(
        [
            "uv", "pip", "install", "--python-platform", "x86_64-pc-windows-msvc",
            "--python-version", "3.12", "--target", str(SITE),
            "-r", str(ROOT / "requirements-standalone.txt"),
        ],
        check=True,
        cwd=ROOT,
    )

    pth = runtime / "python312._pth"
    lines = [line for line in pth.read_text().splitlines()
             if line.strip() not in {"import site", "Lib\\site-packages"}]
    pth.write_text("\n".join([*lines, "Lib\\site-packages", "import site"]) + "\n")

    engine = OUT / "engine"
    engine.mkdir()
    for name in ("standalone.py", "legacy_gaze.py", "platform_io.py", "face_landmarker.task"):
        shutil.copy2(ROOT / "engine" / name, engine / name)

    launcher = (
        "@echo off\nsetlocal\ncd /d \"%~dp0\"\n"
        "runtime\\python.exe -u engine\\standalone.py %*\n"
        "if errorlevel 1 (\n  echo.\n"
        "  echo SeeOSK 실행에 실패했습니다. 카메라 권한과 Windows 10/11 x64를 확인하세요.\n"
        "  pause\n  exit /b 1\n)\nendlocal\n"
    )
    (OUT / "SeeOSK_실행.bat").write_bytes(launcher.replace("\n", "\r\n").encode("utf-8"))
    (OUT / "실행안내.txt").write_text(
        "SeeOSK Windows x64 휴대형 실행본\n\n"
        "압축을 푼 뒤 SeeOSK_실행.bat을 더블클릭하세요. Python과 라이브러리가 "
        "포함되어 있어 별도 설치와 인터넷 연결은 필요하지 않습니다.\n"
        "Windows 10/11 64비트용입니다. 카메라 개인정보 설정에서 데스크톱 앱의 "
        "카메라 접근을 허용하세요.\n"
        "Windows 실기기에서 카메라와 커서 동작을 검증하지 못했습니다.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
