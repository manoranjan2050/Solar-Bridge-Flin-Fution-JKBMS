#!/bin/bash -e
# Copy the Solar Bridge source (staged into files/solar-bridge by build-os.sh)
# into the image so the next sub-stage can run the installer inside the chroot.

rm -rf "${ROOTFS_DIR}/opt/solar-bridge-src"
mkdir -p "${ROOTFS_DIR}/opt/solar-bridge-src"
cp -a files/solar-bridge/. "${ROOTFS_DIR}/opt/solar-bridge-src/"
