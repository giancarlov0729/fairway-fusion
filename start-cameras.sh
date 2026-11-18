#!/bin/sh
set -eu

cd "$(dirname "$0")"
bench_python=${FAIRWAY_BENCH_PYTHON:-"$PWD/.venv-bench/bin/python"}
if [ ! -x "$bench_python" ]; then
    printf '%s\n' 'Camera environment missing. Set up .venv-bench using docs/camera_first_demo.md.' >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    set -- --fps 30 --exposure-us 2000 --gain 4 \
        --min-radius 24 --max-radius 44 \
        --roi 0 0 262 640 140 \
        --roi 1 0 252 640 140
fi

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$bench_python" scripts/camera_bench.py "$@"
