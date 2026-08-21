# SMTP setup for τrend (email verification)

τrend will **not** create accounts until SMTP works. After someone registers, the app emails a link like:

`https://tau.bposhaughnessy.com/api/auth/verify-email?token=…`

You configure this in **`~/trend-multi/secrets.env` on the Linode** (never commit that file).

---

## Recommended for you: DreamHost mailbox

Your DNS is already on DreamHost, so the simplest path is a real DreamHost address that can send mail (e.g. `noreply@bposhaughnessy.com` or `tau@bposhaughnessy.com`).

### 1. Create a mailbox in DreamHost

1. Log into [panel.dreamhost.com](https://panel.dreamhost.com/)
2. Go to **Mail** → **Manage Email** (wording may be “Email Addresses”)
3. **Create** an address on `bposhaughnessy.com`, for example:
   - `noreply@bposhaughnessy.com`, or
   - `tau@bposhaughnessy.com`
4. Set a strong password and save it somewhere safe  
   (this is the SMTP password — not the Caddy `beta` gate password)

Official overview: [Email client configuration](https://help.dreamhost.com/hc/en-us/articles/214918038-Email-client-configuration-overview)

### 2. Put these values in `secrets.env` on the Linode

SSH in and edit:

```bash
ssh -t breandan@72.14.190.14
nano ~/trend-multi/secrets.env
```

Set (adjust the address to the one you created):

```bash
PUBLIC_BASE_URL=https://tau.bposhaughnessy.com

SMTP_HOST=smtp.dreamhost.com
SMTP_PORT=587
SMTP_USER=noreply@bposhaughnessy.com
SMTP_PASSWORD=the-mailbox-password-you-just-set
SMTP_FROM=τrend <noreply@bposhaughnessy.com>
SMTP_STARTTLS=true
SMTP_SSL=false
```

Notes:

| Variable | Meaning |
|----------|---------|
| `SMTP_HOST` | DreamHost outbound server |
| `SMTP_PORT` | `587` + STARTTLS (recommended) |
| `SMTP_USER` | **Full** email address |
| `SMTP_PASSWORD` | That mailbox’s password |
| `SMTP_FROM` | What recipients see as From — use the **same domain** as the mailbox |
| `SMTP_STARTTLS` | `true` for port 587 |
| `SMTP_SSL` | `false` for port 587 (use `true` only with port `465`) |
| `PUBLIC_BASE_URL` | Must be your public HTTPS URL (already set for tau) |

**Port 465 alternative** (if 587 is blocked somehow):

```bash
SMTP_PORT=465
SMTP_STARTTLS=false
SMTP_SSL=true
```

### 3. Restart the app so it reloads secrets

```bash
sudo systemctl restart trend-multi
```

(`secrets.env` is only read at process start.)

### 4. Confirm SMTP is seen as configured

```bash
curl -sS https://tau.bposhaughnessy.com/api/health | python3 -m json.tool
```

You want `"smtp_configured": true`.

### 5. End-to-end test

1. Open https://tau.bposhaughnessy.com  
2. **Create account** with a real inbox you can open  
3. Check email for the verification message  
4. Click the link → should land on login with “Email verified”  
5. Sign in  

If mail never arrives: spam folder, wait a minute, check logs:

```bash
sudo journalctl -u trend-multi -n 80 --no-pager
```

Look for SMTP / authentication errors after clicking Create account.

---

## What τrend does with SMTP

1. You click **Create account**
2. App creates an **unverified** user (no session yet)
3. App connects to `SMTP_HOST` and sends one message
4. You click the link → `email_verified=true` → you can sign in

Without `SMTP_HOST` + `SMTP_FROM` (+ usually user/pass), registration returns **503** and the login page disables Create account.

---

## Alternative A — Gmail “App password” (quick personal tests only)

Fine for closed beta if you don’t want a DreamHost mailbox yet. Not ideal for production volume.

1. Google Account → Security → enable **2-Step Verification**
2. Create an **App password** (Mail / Other)
3. In `secrets.env`:

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=yourname@gmail.com
SMTP_PASSWORD=the-16-char-app-password
SMTP_FROM=τrend <yourname@gmail.com>
SMTP_STARTTLS=true
SMTP_SSL=false
```

Gmail may flag or rate-limit automated mail; DreamHost domain mail is cleaner for `bposhaughnessy.com` users.

---

## Alternative B — Mailgun / Postmark / Amazon SES (scale later)

Transactional providers give better deliverability once you have more signups.

Typical pattern:

1. Verify domain `bposhaughnessy.com` (DNS TXT/CNAME they give you — add in DreamHost DNS Settings)
2. Create SMTP credentials in their dashboard
3. Put their `SMTP_HOST` / user / pass into `secrets.env`
4. Set `SMTP_FROM` to an address on the verified domain (e.g. `noreply@bposhaughnessy.com`)

Until you need that, **DreamHost mailbox is enough**.

---

## Checklist

- [ ] DreamHost mailbox created  
- [ ] `SMTP_*` filled in `~/trend-multi/secrets.env`  
- [ ] `PUBLIC_BASE_URL=https://tau.bposhaughnessy.com`  
- [ ] `sudo systemctl restart trend-multi`  
- [ ] `/api/health` shows `smtp_configured: true`  
- [ ] Register → email arrives → verify link → login works  

Admin escape hatch: on `/admin`, **Verify email** can mark a user verified if mail is broken during testing.

---

## Mail going to spam

Usually **SPF / DKIM / DMARC**, not the app code.

### Check SPF

```bash
dig +short bposhaughnessy.com TXT | grep spf
```

Example problem: SPF only allows Brevo/Sendinblue:

```text
v=spf1 include:spf.sendinblue.com mx ~all
```

…but τrend sends through **DreamHost** (`smtp.dreamhost.com`). Receivers then fail SPF → spam.

**Fix:** one combined SPF TXT on the apex domain (DreamHost → DNS Settings). Do **not** add a second `v=spf1` record — edit the existing one:

```text
v=spf1 include:spf.sendinblue.com include:netblocks.dreamhost.com include:relay.mailchannels.net mx ~all
```

Also:

- Keep `SMTP_FROM` on the same mailbox as `SMTP_USER` (e.g. `trend <tau@bposhaughnessy.com>`)
- Prefer ASCII in the From display name
- After DNS change, wait a few minutes and resend
- In Gmail: message → ⋮ → **Show original** → confirm `SPF: PASS` / `DKIM: PASS`
