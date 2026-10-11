#!/usr/bin/env bash
# Rebuild public/models/beaver.glb (and blender/beaver.blend) from build_beaver.py.
#
#   BLENDER=/path/to/blender npm run model
#
# From WSL, point BLENDER at the Windows blender.exe; paths are converted.
set -euo pipefail
cd "$(dirname "$0")/.."

BLENDER="${BLENDER:-blender}"
path() {
  if [[ "$BLENDER" == *.exe ]]; then
    echo "$(wslpath -w "$(dirname "$1")")\\$(basename "$1")"
  else
    echo "$1"
  fi
}

"$BLENDER" -b --factory-startup --python "$(path blender/build_beaver.py)" -- \
  --glb "$(path blender/beaver.raw.glb)" \
  --blend "$(path blender/beaver.blend)" \
  "$@"

# Quantize and meshopt-compress for the web (~1.2 MB -> ~350 KB).
npx -y @gltf-transform/cli@4 meshopt blender/beaver.raw.glb public/models/beaver.glb
rm blender/beaver.raw.glb
