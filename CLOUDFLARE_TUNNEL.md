# 🌐 Public custom domain via Cloudflare Tunnel

Make the dashboard reachable at **`https://solar.manoranjan.dev`** from any browser
in the world — with automatic HTTPS, **no port forwarding**, and a login gate so your
inverter controls stay protected.

> `cloudflared` is already installed on your Pi. You just run one script + a few clicks
> in the Cloudflare dashboard. ~15 minutes total.

---

## Why Cloudflare Tunnel (not port-forwarding)

- Your Pi makes an **outbound** connection to Cloudflare — nothing is opened on your router
- **Automatic HTTPS** (required: `.dev` domains are HTTPS-only in every browser)
- Works behind home NAT / CGNAT
- **Cloudflare Access** can put a Google/email login *in front* of the whole site — essential because this dashboard can control your inverter

---

## Step 0 — Put `manoranjan.dev` on Cloudflare (one-time, free)

The tunnel needs your domain's DNS to be managed by Cloudflare.

1. Sign up free at **https://dash.cloudflare.com** → **Add a site** → `manoranjan.dev`
2. Choose the **Free** plan → Cloudflare imports your existing DNS records
3. Cloudflare shows you **2 nameservers** (e.g. `xxx.ns.cloudflare.com`)
4. Go to **where you registered `manoranjan.dev`** (Google Domains / Squarespace / your registrar) → replace its nameservers with Cloudflare's two
5. Wait for Cloudflare to say the domain is **Active** (usually 5 min–2 h; it emails you)

> Already on Cloudflare? Skip this step entirely.

---

## Step 1 — Run the setup script on the Pi

SSH to the Pi (or use the dashboard's nothing — this part needs a terminal once):

```bash
cd /opt/solar-bridge
bash setup_cloudflare.sh solar.manoranjan.dev
```

What it does, with prompts:

1. **Login** — prints a URL. Open it in any browser, log into Cloudflare, and **pick `manoranjan.dev`**. (This authorises just this Pi.)
2. Creates a tunnel named `solar-bridge`
3. Writes `/etc/cloudflared/config.yml` (maps `solar.manoranjan.dev` → `localhost:8080`)
4. Creates the DNS record automatically
5. Installs `cloudflared` as a boot service and starts it

When it finishes, open **https://solar.manoranjan.dev** — your dashboard, with a padlock. 🎉
(First load can take 1–2 minutes while DNS propagates.)

---

## Step 6 — 🔒 Lock it down (DO THIS — the URL is public!)

Right now anyone who finds the URL only faces your dashboard password. Add **Cloudflare
Access** so they must pass a Google/email login *before even reaching* the dashboard —
free, and it's the difference between "safe" and "my inverter is on the open internet".

1. Cloudflare dashboard → **Zero Trust** (left menu) → it may ask you to pick a free team name once
2. **Access → Applications → Add an application → Self-hosted**
3. Application config:
   - **Application name:** `Solar Bridge`
   - **Session duration:** `1 month` (so you don't log in constantly)
   - **Application domain:** subdomain `solar`, domain `manoranjan.dev`
4. **Add policy:**
   - **Policy name:** `Only me`
   - **Action:** `Allow`
   - **Include → Emails →** your email (e.g. `electroiot.in@gmail.com`)
   - (Add family members' emails here too if you want)
5. Save. Now visiting `solar.manoranjan.dev` first shows a Cloudflare login → after you
   verify your email/Google, *then* the dashboard. Bots never get past the gate.

> Result: **two locks** — Cloudflare Access (who can reach the site) + your dashboard
> password (second factor). For something that controls an inverter, keep both on.

### Optional extra hardening
- **WAF / rate-limit:** Cloudflare free plan auto-blocks a lot; you can add a rate-limit rule under Security → WAF.
- **Keep a strong dashboard password** (System page → Change Login). Defence in depth.
- **Telegram audit** already messages you on every settings change — you'll see any access that makes changes.

---

## Managing it

```bash
# status / logs
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -f

# restart after a config change
sudo systemctl restart cloudflared

# the mapping (hostname → local port)
cat /etc/cloudflared/config.yml

# list / delete tunnels
cloudflared tunnel list
cloudflared tunnel delete solar-bridge     # to start over
```

### Pointing more subdomains at the Pi (e.g. Home Assistant)
Edit `/etc/cloudflared/config.yml`, add another ingress entry above the `404` line:
```yaml
ingress:
  - hostname: solar.manoranjan.dev
    service: http://localhost:8080
  - hostname: ha.manoranjan.dev          # ← new
    service: http://192.168.1.82:8123     # your HA
  - service: http_status:404
```
Then `cloudflared tunnel route dns solar-bridge ha.manoranjan.dev` and
`sudo systemctl restart cloudflared`. (Gate each with its own Access app.)

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `setup_cloudflare.sh` login URL won't authorise | The domain isn't Active on Cloudflare yet (Step 0). Wait for the "Active" email, retry. |
| `https://solar.manoranjan.dev` → 502/error 1033 | Tunnel not running or wrong port: `sudo systemctl status cloudflared`; confirm dashboard is on 8080 (`curl -s localhost:8080/login`). |
| Site loads but no live data / websocket errors | Cloudflare proxies WebSockets by default — fine. If issues, ensure the orange cloud is ON for the record (it is, via the tunnel). |
| "Too many redirects" | In Cloudflare → SSL/TLS → set encryption mode to **Full** (not Flexible). |
| DNS record already exists error | Harmless — the script reuses it. Or delete the old `solar` record in Cloudflare DNS and re-run. |
| Want it private again | Delete the Access app + `cloudflared tunnel delete solar-bridge`; use Tailscale instead. |

---

## Now you have all three ways in

| Reachable at | How | Who |
|---|---|---|
| `http://192.168.1.x:8080` | LAN | Home WiFi only |
| `http://openwifi.tail8e81a4.ts.net/` | Tailscale | Your devices, anywhere |
| **`https://solar.manoranjan.dev`** | **Cloudflare Tunnel** | **Public (gated by Access)** |

Install the **PWA from the HTTPS URL** for a true full-screen app: open
`https://solar.manoranjan.dev` in Chrome → menu → *Install app*.
