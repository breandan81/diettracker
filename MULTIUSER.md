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

## Roadmap (from plan)

- [x] B0 clone + port 8511
- [x] B1 FastAPI + models (+ SQLite bootstrap / Postgres-ready)
- [ ] B2 Auth (email + Google)
- [ ] B3 Admin UI
- [ ] B4–B5 Grok coach + quotas
- [ ] B6–B7 Ingest tokens + About / ESP zip
- [ ] B8 Linode deploy scripts
