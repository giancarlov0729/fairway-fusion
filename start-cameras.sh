#!/bin/sh
set -eu

cd "$(dirname "$0")"
bench_python=${FAIRWAY_BENCH_PYTHON:-"$PWD/.venv-bench/bin/python"}
if [ ! -x "$bench_python" ]; then
    printf '%s\n' 'Camera environment missing. Set up .venv-bench using docs/camera_first_demo.md.' >&2
    exit 1
fi

if [ "${1:-}" = "--locked-demo" ]; then
    shift
    set -- --ball-lock --pico-port auto --fps 60 --exposure-us 1000 --gain 8 \
        --min-radius 6 --max-radius 56 --acquire-min-radius 18 "$@"
elif [ "${1:-}" = "--chip-demo" ]; then
    shift
    use_pico=true
    for arg do
        if [ "$arg" = "--vision-trigger" ]; then use_pico=false; fi
    done
    if "$use_pico"; then set -- --pico-port auto "$@"; fi
    set -- --fps 60 --exposure-us 1000 --gain 8 \
        --min-radius 6 --max-radius 56 --acquire-min-radius 18 "$@"
elif [ "$#" -eq 0 ]; then
    set -- --fps 60 --exposure-us 1000 --gain 8 \
        --min-radius 12 --max-radius 56 --acquire-min-radius 18
fi

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$bench_python" scripts/camera_bench.py "$@"
