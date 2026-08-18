# Pizzeria Mari Dough Log

A private, Pi-ready dough calculator and service-day production journal. Each log preserves the exact formula used for one service date, even when its source recipe template changes later.

## Included

- Baker's-percentage calculator based on dough-ball count and weight, defaulting to 700 g balls
- Flour blends with protein and ash content, plus weighted overall protein and ash summaries
- Yeast types, custom ingredients, bowl-residue compensation, and formula weights in grams and pounds
- Any number of independently configured preferments, each expressed as a percentage of total flour with its own multi-flour blend, water, leavening, and notes
- Automatic deduction of every preferment from the final mix
- Reusable recipe templates and immutable service-day formula snapshots
- Mix date/time, room temperature, humidity, ingredient temperatures, desired/actual final dough temperature, staged mixing speeds/durations, summed total mix time, and mix notes
- Post-service rating, service notes, and finished-pizza photo uploads
- Reusable flour library with protein and ash values that autofill recipe flour pick lists
- At-a-glance history metrics for hydration, flour blend, IDY, protein, and ash
- Multiple finished-pizza photos, including iPhone HEIC/HEIF support
- History search/filtering, print layout, JSON record export, and a health-check endpoint
- Pizzeria Mari's Compagnon and Semplicita typography, cream horizontal logo, and shared blue/cream/orange/green visual system
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
   ```

2. Put the generated value after `SECRET_KEY=` in `.env`.

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

The service uses two Gunicorn processes with SQLite WAL mode and a busy timeout, which is appropriate for the small number of devices expected to use this private app.

## Optional Nginx access

`deploy/nginx-dough-log.conf` contains a reverse-proxy example for `doughlog.pizzeriamari.com`. Replace the hostname if needed, copy it to `/etc/nginx/sites-available/dough-log`, enable it, and obtain the certificate with Certbot as you did for the Service Dashboard.

Because this app contains internal production records and photos, add authentication before exposing it to the public Internet. Keeping it LAN-only initially requires no Nginx or certificate.

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
