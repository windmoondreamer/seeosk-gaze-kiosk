# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('/Users/jangjaewon/Downloads/대외활동/11_창의문제해결/최종아이디어/seeosk_mac/engine/face_landmarker.task', '.')]
datas += collect_data_files('mediapipe')


a = Analysis(
    ['/Users/jangjaewon/Downloads/대외활동/11_창의문제해결/최종아이디어/seeosk_mac/engine/worker.py'],
    pathex=['/Users/jangjaewon/Downloads/대외활동/11_창의문제해결/최종아이디어/seeosk_mac/engine'],
    binaries=[],
    datas=datas,
    hiddenimports=['AVFoundation', 'Foundation', 'onnxruntime'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tensorflow', 'mediapipe.model_maker', 'torch', 'torchvision', 'ultralytics', 'jax', 'jaxlib', 'polars', 'scipy', 'onnx', 'onnxslim'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SeeOSKEngine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SeeOSKEngine',
)
