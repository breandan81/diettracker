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
- [ ] B3 Admin UI
- [ ] B4–B5 Grok coach + quotas
- [ ] B6–B7 Ingest tokens + About / ESP zip
- [ ] B8 Linode deploy scripts
