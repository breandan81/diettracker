# Deploy τrend multi-user (Linode / Ubuntu)

Target shape: **rsync from laptop → Ubuntu VPS**, **systemd** runs uvicorn, **Caddy** terminates TLS and reverse-proxies to `127.0.0.1:8511`.

SSH hardening scripts in this folder are separate (`setup-ssh-keypair.sh`, `harden-ssh-server.sh`).

## One-time on the server

```bash
# From your laptop (after SSH keys work):
scp deploy/remote-bootstrap.sh breandan@YOUR_LINODE_IP:~/
ssh breandan@YOUR_LINODE_IP 'bash remote-bootstrap.sh'

# Or copy the whole repo once, then:
#   cd ~/trend-multi && bash deploy/remote-bootstrap.sh
```

Set DNS A/AAAA for your domain to the Linode IP before (or right after) bootstrap so Caddy can get a cert.

Edit secrets on the server (never commit):

```bash
ssh breandan@YOUR_LINODE_IP
nano ~/trend-multi/secrets.env   # from secrets.example.env
# Required for public signup: SMTP_* + PUBLIC_BASE_URL=https://your.domain
sudo systemctl restart trend-multi caddy
```

**SMTP walkthrough (DreamHost mailbox, Gmail app password, etc.):** [`SMTP.md`](./SMTP.md)

## Deploy updates from your laptop

```bash
cd ~/AIML/claude/trend-multi
./deploy/deploy.sh breandan@YOUR_LINODE_IP
# optional:
#   REMOTE_DIR=~/trend-multi ./deploy/deploy.sh breandan@host
#   SKIP_RESTART=1 ./deploy/deploy.sh breandan@host   # rsync only
```

`deploy.sh` rsyncs code (excludes `.venv`, `data/`, `secrets.env`, local DBs), runs `pip install -r requirements.txt` remotely, and restarts `trend-multi`.

## Closed-beta password (whole site)

Until you’re happy, put **HTTP basic auth** on Caddy so strangers never see the app (or even `/login`). This is separate from τrend user accounts.

```bash
# On a machine with caddy or the project venv:
./deploy/caddy-hash-password.sh 'pick-a-shared-password'
# → prints:  beta $2a$14$...

# On the Linode, edit /etc/caddy/Caddyfile — set the hash in basic_auth { }, then:
sudo systemctl reload caddy
```

Visit `https://your.subdomain` → browser asks for user **`beta`** + that password → then normal τrend login.

**To open publicly later:** delete the `basic_auth { ... }` block from the Caddyfile and `sudo systemctl reload caddy`.

**ESP32 note:** with whole-site basic auth, the scale POSTs need the same credentials (or turn the gate off / switch to path exceptions later). Fine to leave ESP on LAN `:8511` until you open the site.

## Checklist (Stage 1)

1. SSH key-only + firewall (see `harden-ssh-server.sh`)
2. `remote-bootstrap.sh` → Caddy + systemd
3. Set Caddy **basic_auth** (closed beta) — see above
4. `secrets.env` on server: `SESSION_SECRET`, `ADMIN_USER_IDS`, `PUBLIC_BASE_URL`, SMTP, optional `XAI_API_KEY`
5. `./deploy/deploy.sh …`
6. Smoke: HTTPS → basic auth → register (needs SMTP) → verify link → login → log weight
7. Keep personal `:8510` / `hackers-diet` as your private copy if you want

## Postgres (optional)

SQLite under `~/trend-multi/data/` is fine for closed beta. For Postgres later, install locally or use Docker, set `DATABASE_URL=postgresql+psycopg://…` in `secrets.env`, restart the unit.
