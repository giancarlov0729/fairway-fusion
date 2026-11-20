#!/bin/sh
set -eu

cd "$(dirname "$0")"
bench_python=${FAIRWAY_BENCH_PYTHON:-"$PWD/.venv-bench/bin/python"}
if [ ! -x "$bench_python" ]; then
    printf '%s\n' 'Camera environment missing. Set up .venv-bench using docs/camera_first_demo.md.' >&2
    exit 1
fi

if [ -z "${FAIRWAY_PICO_PORT:-}" ]; then
    set -- /dev/serial/by-id/usb-*Pico*-if00
    if [ "$#" -ne 1 ] || [ ! -c "$1" ]; then
        printf '%s\n' 'Connect one Pico over USB, or set FAIRWAY_PICO_PORT to its serial path.' >&2
        exit 1
    fi
    FAIRWAY_PICO_PORT=$1
fi

export FAIRWAY_PICO_PORT FAIRWAY_LIVE_CAPTURE=1
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
printf '%s\n' 'Open http://127.0.0.1:5000/live for Live Capture.'
exec "$bench_python" -m flask --app fairway_fusion.web.app:create_app run \
    --host 127.0.0.1 --port 5000 --no-reload
