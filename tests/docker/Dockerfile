# The Linux tiers, locally, as CI runs them (.github/workflows/tests.yml,
# ubuntu-24.04). Built and run by tests/docker/run.sh - nothing to do by hand.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Same input stack as CI's "Linux input stack for the hostsim tier", plus what
# CI gets from its setup actions (python, node) and evdev's build needs.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        python3 python3-pip python3-dev gcc rsync ca-certificates nodejs \
        xvfb ibus ibus-gtk3 python3-gi gir1.2-gtk-3.0 python3-xlib x11-xkb-utils dbus-x11 g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -q -r /tmp/requirements.txt \
    && python3 -m playwright install --with-deps chromium \
    && rm -rf /root/.cache/pip
