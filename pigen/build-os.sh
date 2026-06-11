#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║   SOLAR BRIDGE OS — image builder (run on Ubuntu/Debian x86_64)          ║
# ║                                                                          ║
# ║   Usage:   bash pigen/build-os.sh                                       ║
# ║   Output:  ../pi-gen/deploy/<date>-SolarBridgeOS.img.xz                 ║
# ║                                                                          ║
# ║   Needs:   docker.io, git, rsync, ~20 GB free disk, internet            ║
# ║   Time:    ~40–90 min (ARM emulation is slow — that's normal)           ║
# ╚══════════════════════════════════════════════════════════════════════════╝
set -e

R='\033[0;31m' G='\033[0;32m' Y='\033[1;33m' C='\033[0;36m' N='\033[0m'
ok()   { echo -e "${G}✓ $*${N}"; }
info() { echo -e "${C}▶ $*${N}"; }
err()  { echo -e "${R}✗ $*${N}"; exit 1; }

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIGEN_SRC="$REPO_DIR/pigen"
PIGEN_DIR="${PIGEN_DIR:-$REPO_DIR/../pi-gen}"     # pi-gen checkout lives NEXT TO the repo
PIGEN_BRANCH="${PIGEN_BRANCH:-arm64}"             # 64-bit images for Pi 4/5

# ── Pre-flight checks ─────────────────────────────────────────────────────────
command -v git    >/dev/null || err "git missing:    sudo apt install -y git"
command -v rsync  >/dev/null || err "rsync missing:  sudo apt install -y rsync"
command -v docker >/dev/null || err "docker missing: sudo apt install -y docker.io"
docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1 || \
    err "Docker daemon not running: sudo systemctl start docker"

FREE_GB=$(df --output=avail -BG "$REPO_DIR/.." | tail -1 | tr -dc '0-9')
[[ "$FREE_GB" -lt 15 ]] && err "Only ${FREE_GB}GB free — need ~20GB for the build"

# ── 1. Get pi-gen ─────────────────────────────────────────────────────────────
if [[ ! -d "$PIGEN_DIR" ]]; then
    info "Cloning pi-gen ($PIGEN_BRANCH branch) → $PIGEN_DIR"
    git clone --branch "$PIGEN_BRANCH" --depth 1 \
        https://github.com/RPi-Distro/pi-gen.git "$PIGEN_DIR"
else
    ok "pi-gen already present at $PIGEN_DIR"
fi

# ── 2. Install our custom stage + config into pi-gen ──────────────────────────
info "Installing stage-solar into pi-gen..."
rm -rf "$PIGEN_DIR/stage-solar"
cp -a "$PIGEN_SRC/stage-solar" "$PIGEN_DIR/stage-solar"
cp    "$PIGEN_SRC/config"      "$PIGEN_DIR/config"
chmod +x "$PIGEN_DIR/stage-solar/prerun.sh" \
         "$PIGEN_DIR/stage-solar"/*/*-run.sh 2>/dev/null || true

# Only export OUR image (suppress the plain Lite image from stage2)
touch "$PIGEN_DIR/stage2/SKIP_IMAGES"

# ── 3. Stage the Solar Bridge source into the stage (clean copy, no secrets) ──
info "Staging Solar Bridge source (secrets & junk excluded)..."
FILES_DIR="$PIGEN_DIR/stage-solar/01-copy-app/files/solar-bridge"
rm -rf "$FILES_DIR"
mkdir -p "$FILES_DIR"
rsync -a "$REPO_DIR/" "$FILES_DIR/" \
    --exclude '.git' --exclude '.claude' --exclude 'pigen' \
    --exclude 'solarassisant' --exclude 'image' --exclude 'backups' \
    --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'deploy_secrets.py' \
    --exclude '*.img' --exclude '*.zip' --exclude '*.db' --exclude '*.db-*' \
    --exclude 'energy.json' --exclude 'live_state.json' --exclude 'control_queue.json' \
    --exclude '*.log' --exclude '*_log.txt' --exclude 'diag_out.txt' \
    --exclude 'dual_bms.txt' --exclude 'frame_out.txt' --exclude 'passive_out.txt'

# Stamp the version into the image
GIT_VER="$(cd "$REPO_DIR" && git describe --tags --always 2>/dev/null || echo dev)"
echo "Solar Bridge OS ${GIT_VER} (built $(date -u +%Y-%m-%dT%H:%MZ))" > "$FILES_DIR/VERSION"
ok "Source staged (version: $GIT_VER)"

# ── 4. Build ──────────────────────────────────────────────────────────────────
info "Starting pi-gen Docker build — go get a coffee (40–90 min)..."
cd "$PIGEN_DIR"
sudo ./build-docker.sh

# ── 5. Done ───────────────────────────────────────────────────────────────────
echo ""
ok "BUILD COMPLETE!"
echo ""
echo -e "  ${Y}Image:${N}"
ls -lh "$PIGEN_DIR/deploy/"*.img.xz 2>/dev/null || ls -lh "$PIGEN_DIR/deploy/" || true
echo ""
echo -e "  ${C}Next:${N} flash it with Raspberry Pi Imager (Use custom image)"
echo -e "        → boot the Pi → http://solarbridge.local:8080  (login: admin / solar)"
echo ""
echo -e "  ${C}Re-running after a failure:${N}  CONTINUE=1 sudo ./build-docker.sh   (inside $PIGEN_DIR)"
echo -e "  ${C}Full clean rebuild:${N}          sudo rm -rf '$PIGEN_DIR/work' then re-run this script"
