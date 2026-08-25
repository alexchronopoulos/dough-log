# Pizzeria Mari Dough Log

A private, Pi-ready dough calculator and service-day production journal. Each log preserves the exact formula used for one service date, even when its source recipe template changes later.

## Included

- Baker's-percentage calculator based on dough-ball count and weight, defaulting to 700 g balls
- Flour blends with protein and ash content, plus weighted overall protein and ash summaries
- Yeast types, custom ingredients, bowl-residue compensation, and formula weights in grams and pounds
- Any number of independently configured preferments, each expressed as a percentage of total flour with its own multi-flour blend, water, leavening, and notes
- Automatic deduction of every preferment from the final mix
- Reusable recipe templates and immutable service-day formula snapshots
- One selectable default recipe that pre-populates every formula input in future recipes
- Mix date/time, room temperature, humidity, ingredient temperatures, desired/actual final dough temperature, staged mixing speeds/durations, summed total mix time, and mix notes
- Post-service rating, service notes, and finished-pizza photo uploads
- Reusable flour library with mill, protein, and ash values that autofill recipe flour pick lists
- Four-flour blend solver with a mill filter for each selection, targeting overall protein and ash while favoring near-equal portions and enforcing a configurable positive minimum for every flour
- Linked whole-number flour-share sliders that always total 100% and update protein, ash, and target deltas live
- Per-flour locks that hold selected slider percentages while unlocked flours rebalance automatically
- Optional 0% flour minimum for exploring two- and three-flour blends
- At-a-glance history metrics for hydration, flour blend, IDY, protein, and ash
- Side-by-side comparison of any two service-day records, with changed values highlighted and a differences-only view
- Multiple finished-pizza photos, including iPhone HEIC/HEIF support
- History search/filtering, print layout, JSON record export, and a health-check endpoint
- Optional app-level sign-in with a signed 30-day browser session, plus Basic Auth compatibility for existing clients
- Pizzeria Mari's Compagnon and Semplicita typography, cream horizontal logo, black icon favicon, and shared blue/cream/orange/green visual system
- Responsive layouts for desktop, iPhone, iPad, and Android

## Quick local test

```bash
uv sync --frozen
uv run python run.py
```

Open `http://PI_IP_ADDRESS:5050` from a device on the same network.

The database and uploaded photos are created under `instance/`. They are intentionally excluded from source-control updates.

## Raspberry Pi production setup

These instructions assume the project lives at `/home/YOUR_USER/dough-log`.

1. Install the application:

   ```bash
   cd /home/YOUR_USER/dough-log
   uv sync --frozen
   cp .env.example .env
   python3 -c 'import secrets; print(secrets.token_hex(32))'
   python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
   ```

2. Put the first generated value after `SECRET_KEY=` in `.env`. To enable authentication, set a username and put the second generated value after the password field. If the site is served exclusively over HTTPS, also enable secure-only session cookies:

   ```dotenv
   BASIC_AUTH_USERNAME=alex
   BASIC_AUTH_PASSWORD=replace-with-the-generated-password
   SESSION_COOKIE_SECURE=true
   ```

   Both authentication values must be set together. Leaving both blank disables authentication for local development. Keep `SESSION_COOKIE_SECURE=false` when accessing the app directly over plain HTTP on the local network.

3. Edit `deploy/dough-log.service`, replacing `YOUR_USER` in the `User`, `Group`, `WorkingDirectory`, and `EnvironmentFile` lines.

4. Install and start the service:

   ```bash
   sudo cp deploy/dough-log.service /etc/systemd/system/dough-log.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now dough-log
   sudo systemctl status dough-log
   ```

5. Confirm the health check:

   ```bash
   curl http://127.0.0.1:5050/health
   ```

The health endpoint intentionally remains available without authentication so the service and updater can verify that the app is running. The sign-in page and brand assets are also public so the login screen can render. All recipes, logs, photos, and other application data require authentication when enabled.

The service uses two Gunicorn processes with SQLite WAL mode and a busy timeout, which is appropriate for the small number of devices expected to use this private app.

## Enable the 30-day sign-in on an existing Pi installation

Generate a password, then edit the existing `.env` file:

```bash
cd /home/YOUR_USER/dough-log
python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
nano .env
```

Add both values, using the generated password:

```dotenv
BASIC_AUTH_USERNAME=alex
BASIC_AUTH_PASSWORD=replace-with-the-generated-password
SESSION_COOKIE_SECURE=true
```

Restart the application so systemd reloads the environment file:

```bash
sudo systemctl restart dough-log
```

The next visit will show the Pizzeria Mari sign-in page. A successful sign-in creates a signed, HttpOnly browser session that expires after 30 days and is not extended on every visit. The username and password themselves are not stored in the cookie. Signing out, clearing browser cookies, using a private window, changing the configured credentials, or changing `SECRET_KEY` requires another sign-in.

Use `SESSION_COOKIE_SECURE=true` for HTTPS. If the app is only being opened directly over plain HTTP on the local network, use `SESSION_COOKIE_SECURE=false`; a browser will not send a secure-only cookie over HTTP.

To disable authentication again, leave both authentication values blank and restart the service.

## Optional Nginx access

`deploy/nginx-dough-log.conf` contains a reverse-proxy example for `doughlog.pizzeriamari.com`. Replace the hostname if needed, copy it to `/etc/nginx/sites-available/dough-log`, enable it, and obtain the certificate with Certbot as you did for the Service Dashboard.

Nginx does not need its own password configuration. Use HTTPS before exposing the app publicly so the sign-in credentials and session remain protected in transit, and set `SESSION_COOKIE_SECURE=true` once HTTPS is active.

## Backups

The two things that must be backed up together are:

- `instance/dough-log.sqlite3`
- `instance/uploads/`

Run the included backup script manually:

```bash
./scripts/backup.sh
```

It creates a timestamped archive under `backups/` and removes backups older than 30 days. For a nightly backup, add this to the Pi user's crontab:

```cron
15 3 * * * /home/YOUR_USER/dough-log/scripts/backup.sh
```

Copy the resulting archives to another machine or cloud storage. A backup that exists only on the Pi will not help if its storage fails.

## Updating without losing records

Every release includes `update.sh`. Copy the new ZIP into the installed project directory and run:

```bash
cd /home/YOUR_USER/dough-log
./update.sh pizzeria-mari-dough-log-vX.Y.Z.zip
```

If the ZIP is the only ZIP in that directory, `./update.sh` is sufficient. The helper checks the archive, tests the new code before installing it, updates dependencies, restarts `dough-log.service`, verifies the health endpoint, and removes the ZIP after a successful update.

The updater never copies over `.env`, `instance/`, or `backups/`, so the database and uploaded photos remain in place. The database initializer only creates missing tables and indexes; it does not erase existing logs.

## Tests

```bash
uv run pytest
```
