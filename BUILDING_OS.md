# 💿 Building "Solar Bridge OS" — a flashable SD-card image

Build your own ready-to-flash `.img` — exactly like Solar Assistant OS. Users flash it with
Raspberry Pi Imager, boot the Pi, open the dashboard, done. No SSH, no commands.

**What gets baked into the image:**
Raspberry Pi OS Lite 64-bit (Bookworm) + Solar Bridge fully installed (venv, all Python deps,
both systemd services enabled, udev rules, sudoers, avahi, nightly backup timer, hardware
watchdog, journald cap). On first boot everything starts automatically.

---

## 🧰 What you need

| Requirement | Notes |
|---|---|
| **Ubuntu / Debian PC** (x86_64) | Real machine or VirtualBox/VMware VM. **Not WSL2** (loop-device problems). |
| **~20 GB free disk** | The build workspace is large |
| **Internet** | Downloads Debian packages + pip wheels during the build |
| **~40–90 minutes** | ARM is emulated with QEMU — slow is normal |

---

## 🚀 Build steps (on Ubuntu)

### 1. Install the build tools (once)

```bash
sudo apt update
sudo apt install -y git docker.io rsync
sudo systemctl enable --now docker
```

### 2. Clone this repo and run the builder

```bash
git clone https://github.com/manoranjan2050/Solar-Bridge-Flin-Fution-JKBMS.git
cd Solar-Bridge-Flin-Fution-JKBMS
bash pigen/build-os.sh
```

The script does everything:
1. Clones the official **pi-gen** (arm64 branch) next to the repo
2. Copies our custom build stage (`stage-solar`) + config into it
3. Stages a **clean copy of the source** (secrets, logs, databases excluded)
4. Stamps a `VERSION` file from `git describe`
5. Runs `sudo ./build-docker.sh` — the whole image is built inside a Docker container

### 3. Wait ☕ (40–90 min), then grab the image

```
../pi-gen/deploy/<date>-SolarBridgeOS.img.xz
```

---

## 📲 Flashing & first boot (what your users do)

1. Open **Raspberry Pi Imager** → *Choose OS → Use custom* → pick the `.img.xz`
2. **Click the ⚙️ / "Edit settings" button** (important!) and set:
   - **WiFi SSID + password** (skip if using Ethernet)
   - Optionally their own username/password and hostname
   - This works because the image is Raspberry Pi OS based — Imager's customization is fully supported
3. Flash → insert SD into the Pi → power on → wait ~2 minutes on first boot
4. Open **`http://solarbridge.local:8080`** (or the Pi's IP)
5. Sign in and configure from the dashboard — no SSH ever needed:
   - **Network & MQTT** page → MQTT broker / Home Assistant details, static IP
   - **Notifications** page → Telegram bot, alerts
   - **System** page → change the login!

### 🔑 Image defaults

| What | Default |
|---|---|
| Linux user | `solar` / `solarbridge` *(Imager settings override this)* |
| Dashboard login | `admin` / `solar` — **tell users to change it on the System page** |
| Hostname | `solarbridge` → `http://solarbridge.local:8080` |
| SSH | Enabled |
| MQTT broker | `homeassistant.local` placeholder — set the real one in the dashboard |
| Inverter / BMS ports | `/dev/hidraw0` / `/dev/ttyUSB0` (auto-detected udev rules installed) |

---

## ⚙️ Customising the bake (optional)

Environment variables read by the installer in image mode — edit
[`pigen/stage-solar/02-install-app/00-run.sh`](pigen/stage-solar/02-install-app/00-run.sh) to set them:

| Variable | Default | Meaning |
|---|---|---|
| `SOLAR_DASH_PASS` | `solar` | Default dashboard password |
| `SOLAR_MQTT_HOST` | `homeassistant.local` | Pre-filled MQTT broker |
| `SOLAR_INV_PORT` / `SOLAR_BMS_PORT` | `/dev/hidraw0` / `/dev/ttyUSB0` | Device ports |
| `SOLAR_BMS_CELLS` | `16` | Cells per pack |
| `SOLAR_INSTALL_TAILSCALE` | `n` | `1` = bake the Tailscale package in |

Image identity (hostname, user, timezone, SSH) lives in [`pigen/config`](pigen/config).

---

## 🛠️ Build troubleshooting

| Problem | Fix |
|---|---|
| `docker: permission denied` | `sudo usermod -aG docker $USER` then log out/in — or just let the script use sudo |
| Build fails mid-way | Resume where it stopped: `cd ../pi-gen && CONTINUE=1 sudo ./build-docker.sh` |
| Weird state after several failures | Full clean: `sudo rm -rf ../pi-gen/work` then re-run `bash pigen/build-os.sh` |
| `binfmt`/`qemu` errors | `sudo apt install -y qemu-user-static binfmt-support` and retry |
| Very slow pip installs | Normal — ARM emulation. The build uses piwheels so most packages are pre-built |
| Out of disk | The `work/` dir is the hog — needs ~15 GB during build |
| Built image too big to share | It's `.xz` compressed (~600–900 MB). GitHub Releases allows up to 2 GB per file |

**Testing without burning an SD card:** you can mount the image locally to inspect it:
```bash
xz -dk ../pi-gen/deploy/*SolarBridgeOS.img.xz
sudo losetup -fP --show ../pi-gen/deploy/*SolarBridgeOS.img   # note the /dev/loopX
sudo mount /dev/loopXp2 /mnt && ls /mnt/opt/solar-bridge      # the app should be there
sudo umount /mnt && sudo losetup -d /dev/loopX
```

---

## 📦 Publishing a release

```bash
git tag v1.0.0 && git push --tags
```
Then on GitHub → **Releases → Draft a new release** → choose the tag → attach
`deploy/<date>-SolarBridgeOS.img.xz` → publish. Users download + flash.

> **Later (optional):** automate this with GitHub Actions using `usimd/pi-gen-action`
> so every tag builds the image in the cloud — no Ubuntu machine needed.

---

## 🔍 How it works (for the curious)

```
pigen/
├── build-os.sh                      # driver: clones pi-gen, stages everything, builds
├── config                           # pi-gen settings (name, hostname, user, stages)
└── stage-solar/                     # our custom pi-gen stage (runs after stage2 = Lite)
    ├── prerun.sh                    # standard stage bootstrap
    ├── EXPORT_IMAGE                 # "export an image after this stage"
    ├── 00-install-deps/00-packages  # apt packages baked into the image
    ├── 01-copy-app/00-run.sh        # copies the staged source into the image rootfs
    │   └── files/solar-bridge/      # (filled by build-os.sh — clean source copy)
    └── 02-install-app/00-run.sh     # runs install_all.sh inside the image chroot:
                                     #   SOLAR_NONINTERACTIVE=1 SOLAR_USER=solar
```

`install_all.sh` detects image mode (`SOLAR_NONINTERACTIVE=1`):
- no prompts — env vars / defaults instead
- allowed to run as root (the chroot has no other user session)
- skips everything that needs *running* hardware/systemd (`udevadm`, `systemctl start`,
  Tailscale login, USB detection) — services are **enabled** so they start on first boot
- hostname is left to pi-gen's `TARGET_HOSTNAME`

The exact same script still works interactively on a normal Pi — one installer, two modes.
