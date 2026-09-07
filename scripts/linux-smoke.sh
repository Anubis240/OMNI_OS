#!/bin/sh
set -eu
mkdir -p /tmp/omni-home /tmp/omni-package
tar -xzf /input/bundle.tar.gz -C /tmp/omni-package
cd /tmp/omni-home
# docker run --network none enforces no external calls, even from browser helpers.
# xvfb-run supplies DISPLAY/XAUTHORITY; no venv or builder files are mounted.
exec timeout 360 xvfb-run -a /bin/sh -c 'exec /usr/bin/env -i \
    HOME=/tmp/omni-home PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" QT_QPA_PLATFORM=xcb \
    /tmp/omni-package/Omni-OS/Omni-OS --smoke-test --smoke-report /reports/smoke-linux-clean.json'
