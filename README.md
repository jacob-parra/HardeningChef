# HardeningChef

A small web app that builds Linux hardening scripts. You pick the target OS, adjust
the settings from the team's research, and download a Bash script. You can also
fetch it on the target machine with `curl` from a temporary link.

Supported targets: **Ubuntu 26.04 LTS, Debian 13 "Trixie", Kali Rolling 2026.3,
Fedora 45, Rocky Linux 10.2**.

## Run it

```bash
docker build -t hardeningchef .
docker run -d --name hardeningchef -p 8080:8080 hardeningchef
```

Then open <http://localhost:8080>.

### Configuration (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `HC_PUBLIC_URL` | *(from request)* | Base URL used in the curl links, e.g. `https://chef.example.com`. Set this when running behind a reverse proxy. |
| `HC_SHARE_ENABLED` | `1` | Set to `0` to turn off temporary curl links. |
| `HC_SHARE_TTL` | `600` | How long a curl link works, in seconds. |
| `HC_SHARE_MAX` | `500` | Maximum number of links kept at once (oldest dropped first). |
| `HC_RATE_LIMIT` | `30` | Scripts each client IP may generate per minute (`0` = unlimited). |
| `HC_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-*` headers are trusted. |

**Serve it over HTTPS** (put a reverse proxy such as Caddy, nginx or Traefik in
front) before anyone pipes scripts from it into `sudo bash`.

## Using a generated script

```bash
sudo bash hardeningchef-ubuntu-26.04-*.sh            # dry run: shows what would change
sudo bash hardeningchef-ubuntu-26.04-*.sh --apply    # apply
```

Or, from the temporary link the site shows after you build:

```bash
curl -fsSL https://chef.example.com/s/<token> | sudo bash -s -- --apply
```

* Changed files are backed up to `/var/backups/hardeningchef/<timestamp>/`.
* Every action is logged to `/var/log/hardeningchef.log`.
* `--yes` answers confirmation prompts, for unattended runs.
* `--force` overrides the OS-version check and the SSH-lockout check.

Every script has an `HC-PROFILE` line that records the choices it was built
from. Use **Import profile** on the site with the `.sh` file, or with the
downloaded `.json` profile, to load those choices back into the form.

### Temporary curl links

Each build can create a link at `/s/<token>` that serves the script for
`HC_SHARE_TTL` seconds; `/s/<token>.sha256` serves its checksum.

* **Unguessable:** tokens are 144-bit random values.
* **Not cached:** links are served with `Cache-Control: no-store`.
* **Memory only:** links are kept in process memory, which is why the container runs a single worker.
* **Cleared on restart:** restarting the container ends every link.

## How it's organised

```
catalog/os.yaml          target OSes and their per-distro facts (service names, groups, tools)
catalog/sections.yaml    every setting: type, default, limits, justification
app/catalog.py           loads the catalog and validates selections
app/generator.py         renders the script from Jinja templates
app/script/              Bash templates: main.sh.j2 + one file per section, hc-logs, hc-inactive
app/main.py              FastAPI routes, temporary links, security headers, rate limit
app/static/, templates/  the web UI (plain JS, no build step)
tests/                   unit/API tests; every OS's script must pass bash -n and shellcheck
docs/compatibility.md    where and why the implementation changes the research doc
```

To add or change a setting, edit `catalog/sections.yaml`; the form updates
automatically. Then use the new value in the matching
`app/script/sections/<section>.sh.j2`.

All user input is validated against the catalog and shell-quoted before it goes
into a script.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --port 8080
PATH=.venv/bin:$PATH .venv/bin/pytest
```
