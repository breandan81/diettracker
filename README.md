# Hacker's Diet — weight trend tracker

Local webapp in the spirit of John Walker's *The Hacker's Diet*:

- Log daily (or irregular) weigh-ins
- **Time-aware EMA** with configurable half-life (gap-tolerant — no need for daily samples or interpolation)
- Smoothed weight-loss/gain **rate** (lb/week, lb/day)
- Estimated **kcal/day** energy balance from the trend slope (`× 3500`)

## Run

```bash
cd ~/AIML/claude/hackers-diet
python3 server.py          # http://0.0.0.0:8510/
```

User systemd unit:

```bash
systemctl --user enable --now hackers-diet.service
systemctl --user status hackers-diet.service
```

SQLite DB: `data/weights.db`

## Gap math

For a gap of `Δt` days and half-life `H`:

```
decay = 0.5 ** (Δt / H)
trend = (1 - decay) * weight + decay * trend_prev
```

Classic daily α≈0.1 ≈ half-life of ~6.6 days (default **7**).

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/trend` | series + summary |
| GET | `/api/weights` | same series |
| POST | `/api/weights` | `{date, weight, note?}` |
| PUT | `/api/weights/:id` | update |
| DELETE | `/api/weights/:id` | delete |
| GET/PUT | `/api/settings` | `half_life_days`, `goal_weight` |
| GET | `/api/coach/status` | KoboldCPP reachability + model |
| GET | `/api/coach` | last cached pep talk |
| POST | `/api/coach` | `{style?: pep\|roast\|haiku\|brief}` → generate via Kobold |

Env: `KOBOLD_URL` (default `http://127.0.0.1:5001`), `KOBOLD_TIMEOUT` (default 90s).

## Future improvements

See [`FUTURE.md`](./FUTURE.md).
