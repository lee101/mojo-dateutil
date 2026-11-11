#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$repo_dir/dist"
# MD_SRC/MD_OUT let a benchmark or test build a variant of the kernel source
# (for example with the parallel threshold disabled) without touching the
# shipped library.
src="${MD_SRC:-$repo_dir/src/dateutil.mojo}"
out="${MD_OUT:-$repo_dir/dist/libmojo-dateutil.so}"
mojo build --emit shared-lib "$src" -o "$out"
