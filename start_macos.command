#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec open "$ROOT/../../배포/macOS/SeeOSK.app"
