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
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pt'))
shutil.copy2(ROOT/'tests/smoke.js', RESOURCES/'smoke.js')
runtime = ROOT/'build/runtime/SeeOSKEngine'
if runtime.is_dir():
    if (RESOURCES/'runtime').exists(): shutil.rmtree(RESOURCES/'runtime')
    shutil.copytree(runtime, RESOURCES/'runtime', symlinks=True)
    (RESOURCES/'runtime.json').unlink(missing_ok=True)
else:
    (RESOURCES/'runtime.json').write_text(json.dumps({'python': str(ROOT/'.venv/bin/python')}))
(CONTENTS/'PkgInfo').write_bytes(b'APPL????')
with (CONTENTS/'Info.plist').open('wb') as f:
    plistlib.dump({
        'CFBundleExecutable':'SeeOSK','CFBundleName':'SeeOSK',
        'CFBundleDisplayName':'SeeOSK','CFBundleIdentifier':'org.seeosk.gaze.mac',
        'CFBundleVersion':'1','CFBundleShortVersionString':'0.1.0',
        'CFBundlePackageType':'APPL','CFBundleSignature':'????','CFBundleSupportedPlatforms':['MacOSX'],'LSArchitecturePriority':['arm64'],'LSMinimumSystemVersion':'13.3',
        'NSHighResolutionCapable':True,
        'NSCameraUsageDescription':'고개 움직임으로 메뉴를 선택하기 위해 카메라를 사용합니다. 영상은 저장하지 않습니다.',
        'NSPrincipalClass':'NSApplication',
    }, f)
subprocess.run(['xcrun','swiftc','-O','-target','arm64-apple-macosx13.3','-module-cache-path',str(ROOT/'build/module-cache'),
                '-framework','AppKit','-framework','WebKit','-framework','AVFoundation',
                str(ROOT/'native/main.swift'),
                '-o',str(CONTENTS/'MacOS/SeeOSK')],check=True)
for cache in list(APP.rglob('__pycache__')):
    shutil.rmtree(cache)
executable = CONTENTS/'MacOS/SeeOSK'
subprocess.run(['codesign','--force','--sign','-',str(executable)],check=True)
subprocess.run(['codesign','--force','--deep','--sign','-',str(APP)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True)
delivery = ROOT.parent.parent/'배포/macOS/SeeOSK.app'
delivery.parent.mkdir(parents=True, exist_ok=True)
if delivery.is_symlink() and delivery.resolve() == APP.resolve():
    delivery.unlink()
if delivery.exists():
    info = plistlib.loads((delivery/'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') not in ('org.seeosk.gaze.mac', 'org.seeosk.gaze.pointer'):
        raise RuntimeError(f'{delivery} is an unrelated app; refusing to replace it.')
backup = ROOT/'build/previous-delivery.app'
if backup.exists():
    shutil.rmtree(backup)
staging = delivery.with_name('.SeeOSK.delivery.app')
if staging.exists():
    shutil.rmtree(staging)
shutil.copytree(APP, staging)
subprocess.run(['codesign','--force','--deep','--sign','-',str(staging)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(staging)],check=True)
if delivery.exists():
    delivery.rename(backup)
try:
    staging.rename(delivery)
except Exception:
    if backup.exists() and not delivery.exists():
        backup.rename(delivery)
    raise
print(APP)
