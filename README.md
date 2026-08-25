# Oracle Cloud Ampere A1 Instance Creator

An autonomous instance-launcher for Oracle Cloud Infrastructure (OCI) Free Tier that keeps retrying until it wins the capacity lottery. Ampere A1 instances are famously hard to grab in popular regions — this tool handles the hammering for you, politely: rate-limit-aware exponential backoff, automatic failover across availability domains, and a live dashboard so you can watch the attempts.

**Target configuration:** `VM.Standard.A1.Flex` · 4 OCPU · 24 GB RAM · 50 GB boot volume · Ubuntu 22.04

## Features

- **Persistent retry loop** — up to 100 attempts per operation with exponential backoff (2 s → 60 s cap) and ±20 % jitter, so Oracle's rate limiter doesn't flag you as an abuser
- **Multi-AD failover** — automatically iterates through all availability domains in your region when one reports *Out of host capacity*
- **Zero-touch networking** — creates (or reuses) the VCN, subnet, and security list (`ampere-a1-*`) with SSH-only ingress; everything is idempotent, so re-runs don't duplicate resources
- **Live dashboard** — single-file Flask app showing status, attempt counter, current AD, runtime, recent log entries, and full instance details on success
- **Latest image detection** — picks the newest Ubuntu 22.04 image compatible with the A1 shape at launch time
- **No secrets in source** — OCI credentials come from the standard `~/.oci/config`; the instance SSH key is loaded from a file on disk, never pasted into code

## Repository layout

```
├── create_ampere_a1.py   # Main script: launch loop, backoff, AD failover
├── dashboard.py          # Single-file Flask dashboard (HTML/JS inline)
├── oci_config.example    # Template for ~/.oci/config
├── requirements.txt      # oci, cryptography, flask
├── run.sh / run.bat      # One-command startup (venv + deps + both processes)
└── .gitignore            # Keeps credentials, logs, and state files out of git
```

## Requirements

- Python 3.8+
- An [OCI account](https://www.oracle.com/cloud/free/) (Free Tier works)
- OCI API signing key configured via `oci setup config` (or manually — see below)
- An RSA SSH private key on disk (only the derived **public** key is sent to OCI)

## Quick start

### 1. Configure OCI credentials

The script reads the standard OCI config file — API signing keys only, nothing embedded in the repo:

```bash
# Option A: official CLI helper
pip install oci-cli
oci setup config

# Option B: manual
mkdir -p ~/.oci && cp oci_config.example ~/.oci/config
# then fill in user OCID, tenancy OCID, fingerprint, region, and key_file path
chmod 600 ~/.oci/config
```

### 2. Point the script at your SSH private key

By default it uses `~/.ssh/id_rsa`. To use a different key:

```bash
export SSH_PRIVATE_KEY_FILE=/path/to/your_private_key   # Linux/macOS
set SSH_PRIVATE_KEY_FILE=C:\path\to\key.pem             # Windows (cmd)
```

> ⚠️ **Never paste a private key into the source code or commit it.**
> If a private key ever lands in a git repo — even briefly — treat it as
> compromised: remove it from history **and** rotate/delete the key pair.

### 3. Launch everything

```bash
# Linux/macOS — creates venv, installs deps, starts dashboard + creator
./run.sh

# Windows
run.bat
```

Or manually:

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

python dashboard.py &           # dashboard → http://localhost:5050
python create_ampere_a1.py
```

Then open **http://localhost:5050** and watch the hunt.

## Configuration

Edit the constants at the top of `create_ampere_a1.py`:

| Constant | Default | Description |
|---|---|---|
| `OCPU_COUNT` | `4` | OCPUs for the A1 Flex shape |
| `MEMORY_IN_GBS` | `24` | RAM in GB |
| `BOOT_VOLUME_SIZE_IN_GBS` | `50` | Boot volume size |
| `AVAILABILITY_DOMAIN` | `None` | Pin to one AD, or `None` to cycle through all |
| `MAX_RETRIES` | `100` | Retry attempts per operation |
| `INITIAL_DELAY_SECONDS` | `2` | First backoff delay |
| `MAX_DELAY_SECONDS` | `60` | Backoff ceiling |
| `OCI_PROFILE` | `DEFAULT` | Profile inside `~/.oci/config` |

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `SSH_PRIVATE_KEY_FILE` | `~/.ssh/id_rsa` | Private key used to derive the instance's authorized key |
| `DASHBOARD_STATUS_FILE` | `<repo>/dashboard_status.json` | Shared state file between creator and dashboard |

## How the retry engine works

```
for each availability domain in region:
    ensure VCN / subnet / security list exist
    try launch_instance:
        ✓ success → fetch public IP, save instance_details.json, stop
        ✗ 429 rate limit     → backoff (delay × 1.5, capped 60 s, ±20 % jitter) → retry
        ✗ 5xx server error   → same backoff → retry
        ✗ "Out of host capacity" → move to next AD immediately
        after MAX_RETRIES exhausted              → next AD
after all ADs → status=failed, exit non-zero
```

On success the script writes `instance_details.json` (git-ignored) containing the instance OCID, IPs, shape, AD, and region, and the dashboard switches to the details view with a ready-made `ssh ubuntu@<ip>` command.

## Dashboard

- Bound to **127.0.0.1:5050 only** — it has no authentication, so it never listens on external interfaces. For remote access use an SSH tunnel: `ssh -L 5050:localhost:5050 user@host`
- Polls `/api/state` every 2 seconds; state flows from the creator via `dashboard_status.json`
- Shows status badge, attempt count, current AD, runtime, last 50 log entries (timestamped with AD and attempt numbers), and instance details on success

## Security notes

- All OCI credentials live in `~/.oci/config` (API signing key) — nothing sensitive is read from the repo
- The instance SSH private key stays on your disk; only the derived public key is transmitted to OCI
- The dashboard binds to localhost and renders all dynamic data HTML-escaped (no XSS path from API data into the page)
- `.gitignore` blocks credentials (`*.pem`, `*-credentials*.txt`, `config`), logs, `instance_details.json`, and `dashboard_status.json`

## Troubleshooting

| Symptom | Meaning |
|---|---|
| `Out of host capacity` repeatedly | Normal — Free Tier A1 is heavily oversubscribed. The whole point of this tool is to outlast it. |
| Frequent `429` warnings | Expected under sustained retries; backoff grows automatically. |
| `SSH_PRIVATE_KEY_FILE not found` | Set the env var or place a key at `~/.ssh/id_rsa`. |
| `Failed to list availability domains` | Check `~/.oci/config` — wrong OCID/fingerprint, or the API key was rotated. |
| Dashboard empty | Start `dashboard.py` before or together with the creator; they share `dashboard_status.json`. |

## Disclaimer

Automated instance launching must respect Oracle's [Terms of Use](https://www.oracle.com/cloud/free/) and fair-use policies. This tool backs off aggressively when rate-limited and only creates resources in your own tenancy. You are responsible for the resources created in your account — remember that idle Always Free A1 instances may be subject to reclamation.
