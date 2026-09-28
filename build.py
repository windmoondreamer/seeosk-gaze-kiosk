#!/usr/bin/env python3
"""Build a local macOS application using the project Python environment."""
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).absolute().parent
APP = ROOT/'dist/SeeOSK.app'
CONTENTS = APP/'Contents'
RESOURCES = CONTENTS/'Resources'
for folder in (CONTENTS/'MacOS', RESOURCES, ROOT/'build/module-cache'):
    folder.mkdir(parents=True, exist_ok=True)
# srcdoc inherits the app page origin; separate file:// frames have opaque origins.
kiosk_html = (ROOT/'web/kiosk/mega.html').read_text().replace('<head>', '<head><base href="kiosk/">', 1)
(ROOT/'web/kiosk-source.js').write_text('window.KIOSK_HTML = '+json.dumps(kiosk_html)+';\n')
for name in ('engine', 'web', 'assets'):
    shutil.copytree(ROOT/name, RESOURCES/name, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(ROOT/'tests/smoke.js', RESOURCES/'smoke.js')
(RESOURCES/'runtime.json').write_text(json.dumps({'python': str(ROOT/'.venv/bin/python')}))
with (CONTENTS/'Info.plist').open('wb') as f:
    plistlib.dump({
        'CFBundleExecutable':'SeeOSK','CFBundleName':'SeeOSK',
        'CFBundleDisplayName':'SeeOSK','CFBundleIdentifier':'org.seeosk.gaze.mac',
        'CFBundleVersion':'1','CFBundleShortVersionString':'0.1.0',
        'CFBundlePackageType':'APPL','LSMinimumSystemVersion':'13.0',
        'NSHighResolutionCapable':True,
        'NSCameraUsageDescription':'눈동자 위치를 추적하고 키오스크 시선을 보정하기 위해 카메라를 사용합니다. 영상은 저장하지 않습니다.',
        'NSPrincipalClass':'NSApplication',
    }, f)
subprocess.run(['xcrun','swiftc','-O','-module-cache-path',str(ROOT/'build/module-cache'),
                '-framework','AppKit','-framework','WebKit','-framework','AVFoundation',
                str(ROOT/'native/main.swift'),'-o',str(CONTENTS/'MacOS/SeeOSK')],check=True)
for cache in list(APP.rglob('__pycache__')):
    shutil.rmtree(cache)
subprocess.run(['codesign','--force','--sign','-',str(APP)],check=True)
delivery = Path.home()/'Downloads/SeeOSK.app'
if delivery.is_symlink() and delivery.resolve() == APP.resolve():
    delivery.unlink()
if delivery.exists():
    info = plistlib.loads((delivery/'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'org.seeosk.gaze.mac':
        raise RuntimeError('Downloads/SeeOSK.app is an unrelated app; refusing to replace it.')
    for cache in list(delivery.rglob('__pycache__')):
        shutil.rmtree(cache)
shutil.copytree(APP, delivery, dirs_exist_ok=True)
print(APP)
