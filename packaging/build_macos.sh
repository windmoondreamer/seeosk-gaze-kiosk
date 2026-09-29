#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Bundle the original kiosk's JSON worker, then wrap it with its Swift UI.
# The click-region model runs through onnxruntime, so torch/ultralytics stay out of the bundle.
# jax, polars and scipy are transitive dependencies that no engine code path imports.
.venv/bin/pyinstaller --noconfirm --clean --onedir --console --name SeeOSKEngine \
  --distpath "$ROOT/build/runtime" --workpath "$ROOT/build/pyinstaller-engine" \
  --specpath "$ROOT/packaging/macos" --paths "$ROOT/engine" \
  --add-data "$ROOT/engine/face_landmarker.task:." \
  --collect-data mediapipe \
  --hidden-import AVFoundation --hidden-import Foundation --hidden-import onnxruntime \
  --exclude-module tensorflow --exclude-module mediapipe.model_maker \
  --exclude-module torch --exclude-module torchvision --exclude-module ultralytics \
  --exclude-module jax --exclude-module jaxlib --exclude-module polars \
  --exclude-module scipy --exclude-module onnx --exclude-module onnxslim \
  "$ROOT/engine/worker.py"
.venv/bin/python build.py
