# The companion app, on Linux, for real: built and unit-tested here, then its
# X11 capture and Cisco's OpenH264 encoder streamed over WebRTC from a virtual
# two-monitor X desktop to a Python WebRTC viewer that decodes the H.264.
# Built and run by companion/tests/linux/run.sh.
FROM ubuntu:24.04
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      ca-certificates curl build-essential pkg-config bzip2 \
      xvfb x11-xserver-utils x11-apps dbus dbus-x11 \
      python3 python3-venv python3-dev libavdevice-dev libavfilter-dev libopus-dev libvpx-dev libsrtp2-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable \
    && /root/.cargo/bin/rustup component add clippy
RUN python3 -m venv /venv && /venv/bin/pip install -q aiortc==1.9.0 websockets numpy
ENV PATH=/root/.cargo/bin:/venv/bin:$PATH
