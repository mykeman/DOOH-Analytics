#!/usr/bin/env bash
# Download the default open models used by the edge agent.
#
#   scripts/fetch_models.sh [target-dir]      (default /var/lib/dooh-edge/models)
#
# Models and licences:
#   YuNet face detector            OpenCV Zoo           MIT
#   YOLOX-S person/bag detector    OpenCV Zoo           Apache-2.0
#   GoogLeNet age / gender         ONNX Model Zoo       see onnx/models (Levi & Hassner, CVPR-W 2015)
#
# Review each model's licence for your commercial use, and consider replacing
# the age/gender classifiers with models validated on your own audience.
set -euo pipefail

DIR="${1:-/var/lib/dooh-edge/models}"
mkdir -p "$DIR"

ZOO="https://github.com/opencv/opencv_zoo/raw/main/models"
ONNX_ZOO="https://github.com/onnx/models/raw/main/validated/vision/body_analysis/age_gender/models"

fetch() {
  local url="$1" out="$DIR/$2"
  if [[ -s "$out" ]]; then
    echo "exists   $out"
    return
  fi
  echo "fetching $out"
  curl -fL --retry 3 -o "$out.part" "$url"
  # Guard against HTML error pages / Git LFS pointer files being saved as models.
  if [[ $(stat -c %s "$out.part") -lt 100000 ]]; then
    echo "error: $url did not return a model file" >&2
    rm -f "$out.part"
    exit 1
  fi
  mv "$out.part" "$out"
}

fetch "$ZOO/face_detection_yunet/face_detection_yunet_2023mar.onnx" face_detection_yunet_2023mar.onnx
fetch "$ZOO/object_detection_yolox/object_detection_yolox_2022nov.onnx" object_detection_yolox_2022nov.onnx
fetch "$ONNX_ZOO/age_googlenet.onnx" age_googlenet.onnx
fetch "$ONNX_ZOO/gender_googlenet.onnx" gender_googlenet.onnx

echo "Models ready in $DIR"
