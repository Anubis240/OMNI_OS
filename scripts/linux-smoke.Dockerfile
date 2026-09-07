# TEST FIXTURE ONLY. No Python, pip, application dependencies or browser cache.
# This is an Ubuntu 22.04 desktop ABI baseline, not an empty/universal Linux OS.
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libegl1 libegl-mesa0 libgbm1 libasound2 libudev1 \
    xvfb xauth fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && ! command -v python3 && ! command -v pip && ! command -v node \
    && for p in libgtk-3-0 libgtk-4-1 libnss3 libnspr4 libgstreamer1.0-0 libportaudio2 libxcb-cursor0; do \
         if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed'; then exit 1; fi; \
       done
COPY linux-smoke.sh /usr/local/bin/omni-smoke
USER 1000:1000
ENTRYPOINT ["/bin/sh", "/usr/local/bin/omni-smoke"]
