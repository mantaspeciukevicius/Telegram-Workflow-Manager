#!/bin/sh
set -eu
exec python /opt/my-tgcf/worker_service.py "$1"
