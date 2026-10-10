# Docker on Windows: step by step

This gets the full JARVIS stack (API, worker, PostgreSQL, Redis, nginx) running on a Windows 11
laptop. Run `scripts\docker_preflight.ps1` after any step to see what is left; it only reads.

> **Your machine has 3.8 GB RAM.** Docker Desktop plus a database is tight but workable if you
> follow step 3 (cap Docker's memory) and use the low-memory profile (the default in `dc.ps1`).
> Close Chrome/Edge tabs while the stack builds the first time.

## Step 1: Install WSL2 (needs an Administrator PowerShell)

Docker Desktop runs Linux containers inside WSL2.

1. Press **Start**, type `PowerShell`, right-click **Windows PowerShell** -> **Run as administrator**.
2. Run:
   ```powershell
   wsl --install --no-distribution
   ```
   `--no-distribution` installs only the WSL2 engine, not an Ubuntu you don't need (saves ~1 GB).
3. **Restart Windows** when it asks. This is required.

Check: `wsl --status` prints "Default Version: 2" instead of "not installed".

## Step 2: Install Docker Desktop (Administrator PowerShell again)

```powershell
winget install -e --id Docker.DockerDesktop
```

1. Restart if the installer asks.
2. Start **Docker Desktop** from the Start menu. Accept the licence terms.
   (Docker Desktop is free for personal use and small businesses; larger companies need a
   paid plan. Signing in is optional; **Skip** is fine.)
3. Wait for the whale icon in the tray to say **Engine running**. First start takes 1 to 2 minutes.
4. In Docker Desktop -> Settings -> General, confirm **Use the WSL 2 based engine** is ticked.
   On a small machine also untick **Start Docker Desktop when you sign in**, so it only uses
   memory when you want it.

Check: `docker version` shows both a Client and a Server section.

## Step 3: Cap Docker's memory (important on 4 GB)

Docker's VM can grab half your RAM and rarely returns it. Cap it:

```powershell
copy docker\wslconfig.example $env:USERPROFILE\.wslconfig
wsl --shutdown
```

Then quit and reopen Docker Desktop.

Check: `docker info --format "{{.MemTotal}}"` shows roughly 2 GB (about 2000000000).

## Step 4: Verify everything so far

```powershell
powershell -ExecutionPolicy Bypass -File scripts\docker_preflight.ps1
```

All the Docker lines should be `[ OK ]`, including "can pull and run a container".

## Step 5: Secrets and settings

Already done if you ran these once (safe to re-run; they never overwrite):

```powershell
python scripts\make_docker_secrets.py
copy docker\env.docker.example docker\.env.docker
```

* `secrets\*` are files holding the passwords and keys. They are git-ignored and are never copied
  into an image. **Back up `secrets\jarvis_data_key` somewhere separate**; without it, encrypted
  conversations and memories cannot be recovered.
* `docker\.env.docker` holds non-secret settings. The defaults run on `http://localhost:8080`.

## Step 6: Build and start

```powershell
scripts\dc.ps1 up -d --build
```

The first run downloads about 500 MB of images and builds ours: **3 to 8 minutes**. Later starts
take seconds. `dc.ps1` is a thin wrapper that adds the env file and the low-memory profile.

## Step 7: Check it is healthy

```powershell
scripts\dc.ps1 ps
```

Expected: `postgres`, `redis`, `api`, `nginx` are **running** (and `healthy` where shown), `worker`
is **running**, and `migrate` shows **exited (0)**. That `0` is success: it is a one-shot job that
creates the database tables and stops.

```powershell
curl http://localhost:8080/healthz      # {"status":"ok"}
curl http://localhost:8080/readyz       # {"ready":true,"database":true,"redis":true}
```

## Step 8: Create your login and open the dashboard

```powershell
scripts\dc.ps1 run --rm api create-admin
```

Type a username, then a password (12+ characters; typing is hidden). Then open
**http://localhost:8080** and sign in. On **System** you should see Database `postgresql OK`,
Cache `Redis OK`, and `CHAIN VERIFIED`.

## Everyday commands

| Do this | Command |
|---|---|
| See status | `scripts\dc.ps1 ps` |
| Follow logs | `scripts\dc.ps1 logs -f api worker` |
| Stop (keeps your data) | `scripts\dc.ps1 down` |
| Start again | `scripts\dc.ps1 up -d` |
| Rebuild after code changes | `scripts\dc.ps1 up -d --build` |
| **Delete all data** (database too) | `scripts\dc.ps1 down -v` |
| Bigger machine, 2 API replicas | `scripts\dc.ps1 -Full up -d --build` |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `wsl --install` says it needs virtualization | Enable it in BIOS/UEFI (yours reports it is already on) |
| Docker Desktop stuck on "Starting" | `wsl --shutdown`, reopen Docker Desktop; make sure step 1 was followed by a restart |
| Whole PC slow / out of memory | Step 3 not applied; run `wsl --shutdown` and reopen. Close other apps while building |
| `migrate` keeps restarting or `api` never starts | `scripts\dc.ps1 logs migrate`. A password error means you regenerated `secrets\` after the database was first created; the volume still holds the old password. Either restore the old secrets or `scripts\dc.ps1 down -v` to start fresh |
| `Missing docker\.env.docker` | `copy docker\env.docker.example docker\.env.docker` |
| Config error mentioning `https` / `JARVIS_PUBLIC_ORIGIN` | You set `JARVIS_ENV=prod` without TLS. For a laptop test use `JARVIS_ENV=dev` |
| Port 8080 already in use | Change `JARVIS_HTTP_PORT` and `JARVIS_PUBLIC_ORIGIN` in `docker\.env.docker`, then `up -d` |
| Login works but the page stays blank | Open the browser console; usually `JARVIS_PUBLIC_ORIGIN` does not match the address in the address bar |

## What "dev" vs "prod" means here

`JARVIS_ENV=dev` in `docker\.env.docker` only relaxes the https and metrics-token requirements so
you can test over plain http. Secrets are still files, PostgreSQL and Redis are still used,
containers are still read-only and unprivileged, and data is still encrypted. Switch to `prod`
(with a real domain and TLS in front) before exposing this to anyone else.
