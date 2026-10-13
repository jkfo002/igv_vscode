#!/bin/sh
# Compatibility entrypoint: dependencies are now managed by Pixi.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec pixi run --manifest-path "$script_dir/../pixi.toml" setup-remote "$@"
