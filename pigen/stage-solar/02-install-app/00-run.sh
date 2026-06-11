#!/bin/bash -e
# Run the Solar Bridge installer inside the image chroot, non-interactively.
# FIRST_USER_NAME comes from pi-gen's config (default: solar).
# The installer skips everything that needs running hardware/systemd and
# leaves both services enabled so they start on first boot.

on_chroot << CHROOT_EOF
set -e
cd /opt/solar-bridge-src
SOLAR_NONINTERACTIVE=1 \
SOLAR_USER="${FIRST_USER_NAME}" \
SOLAR_DASH_HOST="${TARGET_HOSTNAME}" \
bash install_all.sh

# Keep a copy of the source for re-installs/updates, but slim it down
rm -rf /opt/solar-bridge-src/solarassisant /opt/solar-bridge-src/image
chown -R "${FIRST_USER_NAME}:${FIRST_USER_NAME}" /opt/solar-bridge-src /opt/solar-bridge

# Trim the pip cache so the image stays small
rm -rf /root/.cache/pip /home/${FIRST_USER_NAME}/.cache/pip || true
CHROOT_EOF
