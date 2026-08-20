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
- [ ] B6–B7 Ingest tokens + About / ESP zip
- [ ] B8 Linode deploy scripts
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
