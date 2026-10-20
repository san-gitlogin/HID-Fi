# The Linux tiers, locally, as CI runs them (ubuntu-24.04). Built and run by
# tests/docker/run.sh - nothing to do by hand. The packages come from
# tests/setup_linux.sh, the one list every Linux route uses.
FROM ubuntu:24.04

COPY setup_linux.sh requirements.txt deps.json /tmp/hidfi/
# Two layers, so a failed toolchain download does not redo the rest.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends python3 \
    && /tmp/hidfi/setup_linux.sh --no-firmware \
    && rm -rf /root/.cache/pip
RUN /tmp/hidfi/setup_linux.sh --firmware-only \
    && rm -rf /var/lib/apt/lists/* /root/.arduino15/staging
