# τrend multi-user (dev)

This tree is a **separate clone** of diettracker for public multi-user work.
The personal single-user app stays at `~/AIML/claude/hackers-diet` on **:8510**.

## Run (LAN)

```bash
cd ~/AIML/claude/trend-multi
cp secrets.example.env secrets.env   # optional
./scripts/run_dev.sh                 # http://127.0.0.1:8511/
```

Default DB is **SQLite** at `data/trend_multi.db` so you can develop without sudo.
Production target remains **Postgres** (`DATABASE_URL=postgresql+psycopg://...`).

Optional Postgres via Docker:

```bash
docker compose up -d
# then set DATABASE_URL in secrets.env to the compose URL (port 5433)
```

## Branch

Work happens on `multi-user`. Do not run this against the live `data/weights.db`.

## Deploy (Linode)

See [`deploy/README.md`](./deploy/README.md):

```bash
# On server (once) — DOMAIN + optional closed-beta password:
DOMAIN=trend.example.com BETA_PASS='shared-gate-password' bash deploy/remote-bootstrap.sh

# From laptop (updates):
./deploy/deploy.sh breandan@YOUR_LINODE_IP
```

Caddy (TLS + optional **basic_auth** whole-site gate) → `127.0.0.1:8511` · systemd `trend-multi` · rsync excludes `secrets.env` and `data/`.  
Browser user is `beta` + your `BETA_PASS`. Remove the `basic_auth` block in `/etc/caddy/Caddyfile` when you want it public.

## Backup export / import

Logged-in users can download a **ZIP backup** (History → Download backup) and re-import it (Import backup). Format `trend-export` v1:

```text
trend-export/manifest.json
trend-export/data.json          # settings, weights, photo metadata + analysis
trend-export/photos/<sha>.…     # content-addressed images (+ optional *_proj.*)
```

Import **upserts** (no duplicates): weights by `logged_at` (else date+weight+note); photos by file SHA-256; settings by key. Does not export passwords or ingest token secrets.

## Email verification

Email/password **registration requires SMTP** (`SMTP_HOST`, `SMTP_FROM`, `PUBLIC_BASE_URL`, usually user/pass). Without it, signup returns **503** and the login page disables Create account.

**How to configure (DreamHost-first):** see [`deploy/SMTP.md`](./deploy/SMTP.md).

Flow: register → verification email (48h link) → `/api/auth/verify-email?token=…` → sign in. Google OAuth counts as verified. Admins can **Verify email** manually. Existing DB users were grandfathered verified on migration.

## Login (after migrate)

```bash
# Import personal data from the live single-user DB (does not modify live DB):
.venv/bin/python scripts/migrate_from_sqlite.py \
  --sqlite ~/AIML/claude/hackers-diet/data/weights.db \
  --photos-dir ~/AIML/claude/hackers-diet/data/photos \
  --email breandan@example.com \
  --password 'DevTrend123!' \
  --name Breandan
```

Then open http://127.0.0.1:8511/login.html  
Default migrated account: `breandan@example.com` / `DevTrend123!`  
Set `ADMIN_USER_IDS=1` in `secrets.env`.

Google OAuth: set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and redirect  
`http://127.0.0.1:8511/api/auth/google/callback`.

## Roadmap (from plan)

- [x] B0 clone + port 8511
- [x] B1 FastAPI + models (+ SQLite bootstrap / Postgres-ready)
- [x] B2 Auth (email + Google routes) + user-scoped trend/settings/photos + data migrate
- [x] B3 Admin UI (`/admin`) — users, disable, reset usage, quota defaults
- [x] B4–B5 Grok coach (no Kobold) + daily coach quota enforcement
- [x] Photo uploads invite-only (admin grant / redeem codes; global kill switch)
- [x] B6–B7 Ingest tokens + About / ESP zip (`/about`, `/api/esp/firmware.zip`, Settings tokens)
- [x] B8 Linode deploy scripts (`deploy/` — Caddy + systemd + rsync)
- [x] Email signup + SMTP verification (fail closed without SMTP; Google counts as verified)
- [ ] Pre-Grok moderation API (still required before wide-open public photos)

Admin: http://127.0.0.1:8511/admin (allowlist `ADMIN_USER_IDS`)

---

## Photo uploads = invite-only (until monetized / moderated)

- New accounts: **`photos_allowed=false`** (cannot upload).
- Admins (`ADMIN_USER_IDS`): always allowed.
- Admin → **Allow photos** per user, or **Create photo invite** (user redeems under Photos).
- Global kill switch: **Photos feature enabled** on `/admin`.
- Grok analyze is **not** auto-run on multi-user upload yet (store-only).

## Safety note: photo upload → Grok (do before public launch)

**Risk:** An open (or lightly gated) upload path that stores images and/or sends them to xAI with **your** API key can be abused. Worst-case illegal content (e.g. CSAM) or other ToS-violating material could hit your key/account and create serious legal/compliance exposure. Provider-side filters help but **do not** make an unmoderated pipe safe. This is not legal advice — consult a lawyer before a wide public launch.

**Mitigations to implement before open internet deploy:**
- Auth required for uploads (done directionally)
- Low daily **vision / Imagine** quotas + admin kill switch for uploads/vision
- **No auto-analyze** — upload private by default; “Analyze with Grok” is an explicit click + ToS acknowledgment
- **Moderation API before** any Grok vision/Imagine call (see below)
- Private photos only (no public stranger gallery)
- Invite-only or allowlist at first; fast ban in `/admin`
- Terms prohibiting illegal content; retention/logging sufficient to respond to abuse
- Optional: ship public **without** photo/vision until the above exists

### How moderation APIs work

A **moderation API** is a separate model/service whose job is to classify content (text and/or images) for policy categories — e.g. sexual content, violence, self-harm, hate, and often a dedicated **child-sexual-exploitation** signal. You call it **before** expensive or sensitive downstream steps.

Typical flow for τrend photos:

```text
User uploads image
    → store privately (or hold in quarantine)
    → POST image (or hash/thumbnail) to moderation API
    → if flagged / high risk: reject, do not call Grok, alert admin / ban
    → if clean: allow optional “Analyze with Grok” / Imagine
```

**Inputs:** image bytes or URL; sometimes text captions too.  
**Outputs:** scores or labels per category + an overall `flagged` / recommended action. Thresholds are yours to set (strict for a free public app).  
**Providers:** OpenAI Moderation, Hive, AWS Rekognition, Google Cloud Vision SafeSearch, Sightengine, etc. (pick one with strong CSAM/CSE handling and a clear abuse process). xAI may also refuse some content on their side — treat that as a **backstop**, not the only gate.  
**Important:** Moderation is probabilistic. Combine with auth, quotas, ToS, bans, and “no Grok until user opts in.” Never auto-forward every upload to Grok.

**Follow-up work (not done yet):** wire a moderation provider, admin global disable for vision/uploads, and opt-in analyze + ToS checkbox.

---

## Staged deployment plan (later)

Assumption for the early stages: accounts need email (+ verification when you wire it), photos are **private to the owner**, and uploads stay **invite-only**. There is little incentive for someone to spam you with illegal/unwanted images if nobody else can see them and the account is tied to an email. That is still a **tail risk** (key-holder liability if anything hits Grok, or if law enforcement ever asks about stored bytes), so widen the audience only in stages.

Do **not** skip stages just because “nobody would bother.” Each stage is a deliberate gate.

### Stage 0 — Local / LAN only (now)

**Who:** you (+ maybe household).  
**Where:** `trend-multi` on `:8511` (or LAN IP). Live personal app stays on `:8510`.  
**Photos:** invite-only (or admin-only); no auto-analyze.  
**Done when:** auth, admin, quotas, invite gate feel solid.

### Stage 1 — Linode, closed beta (friends / allowlist)

**Who:** small allowlist or invite codes for *accounts*, not just photos.  
**Where:** VPS + HTTPS (Caddy/nginx), Postgres preferred, secrets in env not git.  
**Hardening:** SSH key-only (scripts already sketched), firewall, backups of DB + photo dir.  
**Photos:** still invite-only; private; store-only (no Grok vision yet, or admin-only test).  
**Ops:** ToS/privacy stub; ability to ban/disable user in `/admin` in one click; daily coach quotas on.  
**Exit criteria:** a few real users for weeks with no abuse incidents; you are comfortable owning the box.

### Stage 2 — Public signup, photos still invite-only

**Who:** anyone can create an account (email verified).  
**Photos:** remain **invite-only** until you decide otherwise. Weight/trend/coach OK for everyone within quotas.  
**Still no:** public galleries, stranger-visible photos, auto-forward to Grok.  
**Add before or during this stage:**
- Email verification required before login / before any upload path matters
- Clear ToS: no illegal content; you may ban and report
- Rate limits on signup + upload endpoints
- Retention note: how long photos live; how to delete account
**Tail-risk posture:** private + invite-only is the main control. Moderation API is **optional** here if you never send user photos to Grok and keep invites rare. Prefer still not calling vision on user uploads.

### Stage 3 — Trusted users can use Grok on *their* photos

**Trigger:** you want Analyze / Imagine for invitees, still private.  
**Required before enabling:**
- Explicit “Analyze with Grok” click + short acknowledgment
- Low daily vision/Imagine quotas + admin kill switch
- **Moderation API before every Grok vision/Imagine call** (and ideally on upload)
- Logging: user id, time, moderation outcome (not necessarily full image forever)
**Still:** no public photo feed; invites or `photos_allowed` for who can upload.

### Stage 4 — Monetize / open photo uploads (wider)

**Trigger:** paid tier or you deliberately open uploads beyond invites.  
**Required:**
- Everything in Stage 3, plus stricter quotas for free tier
- Moderation on **upload** (quarantine or reject), not only on Analyze
- Abuse report path + documented takedown process (even if you are the only admin)
- Consider: lawyer pass on ToS/privacy; age gate; whether you want any sharing features at all
**If you never need public photo sharing:** you can stay at Stage 2–3 indefinitely and treat open uploads as unnecessary risk.

### Stage 5 — Optional sharing / social (only if product needs it)

Public or friend-visible photos change the threat model (harassment, illegal content for an audience). Treat as a **new product decision**, not a default. Requires moderation, reporting, and likely legal review. Easy recommendation: **don’t build this** unless it is core to the product.

---

### Practical “do this on Linode” checklist (Stage 1)

1. Postgres + migrate schema; copy secrets; set `ADMIN_USER_IDS`.
2. Reverse proxy + TLS; app not exposed raw on 8511 to the world if you can avoid it.
3. Backups: DB dump + `data/photos` (or object storage later).
4. Confirm photo kill switch and invite-only before DNS goes public.
5. Smoke: signup → login → log weight → (no photo unless invited) → admin ban test.
6. Keep personal `:8510` / hackers-diet as your private copy if you want a clean split.

### What you can defer

| Item | Defer until |
|------|-------------|
| Moderation API | Stage 3 (first time user photos → Grok), or Stage 2 if you store many untrusted uploads |
| Open photo uploads | Stage 4 / monetization |
| Public galleries | Stage 5 — probably never |
| Fancy DMCA/legal tooling | When you have scale or a real incident process need |
| Google OAuth on the VPS | Whenever; use a real domain redirect URI |

### Bottom line

Email + private photos + invite-only uploads already remove most of the “why would they harass me” motive. Deploy in stages anyway: **closed beta → public accounts without open photos → Grok-on-photos only with moderation → open uploads only if monetized.** Stopping at Stage 2 forever is a valid, low-risk product shape.
