# Oracle Cloud Ampere A1 Instance Creator

An automated script to provision **Ampere A1 Flex** instances (4 OCPU, 24 GB RAM, 50 GB boot volume) on Oracle Cloud Infrastructure with Ubuntu 22.04. Features rate-limit-aware exponential backoff, multi-availability domain failover, and a real-time web dashboard.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Oracle Cloud Infrastructure                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
│  │  AD-1 (Frankfurt)│  │  AD-2 (Frankfurt)│  │  AD-3 (Frankfurt)│      │
│  │  VM.Standard.A1  │  │  VM.Standard.A1  │  │  VM.Standard.A1  │      │
│  │  Flex 4C/24GB    │  │  Flex 4C/24GB    │  │  Flex 4C/24GB    │      │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘      │
│           │                     │                     │                │
│           └─────────────────────┼─────────────────────┘                │
│                                 ▼                                      │
│                    ┌──────────────────────┐                            │
│                    │   VCN: ampere-a1-vcn │                            │
│                    │   CIDR: 10.0.0.0/16  │                            │
│                    │   ┌────────────────┐ │                            │
│                    │   │ Subnet: 10.0.1 │ │                            │
│                    │   │ /24 per AD     │ │                            │
│                    │   └────────────────┘ │                            │
│                    │   Security List:   │                            │
│                    │   SSH (22) + ICMP  │                            │
│                    └──────────────────────┘                            │
└─────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ OCI SDK (REST API)
                                  │
┌─────────────────────────────────────────────────────────────────────────┐
│                           Local Machine                                  │
│  ┌──────────────────┐    ┌──────────────────┐                          │
│  │ create_ampere_a1 │    │   dashboard.py   │                          │
│  │     .py          │    │   (Flask)        │                          │
│  │                  │    │                  │                          │
│  │ • Rate limiting  │    │ • Port 5050     │                          │
│  │ • Exponential    │    │ • Auto-refresh  │                          │
│  │   backoff        │    │   (2s)          │                          │
│  │ • Multi-AD       │    │ • Logs with     │                          │
│  │   failover       │    │   timestamps,   │                          │
│  │ • Auto networking│    │   AD, attempt # │                          │
│  └────────┬─────────┘    └────────┬────────┘                          │
│           │                       │                                    │
│           └───────────────────────┘                                    │
│                    JSON status file                                    │
│           (dashboard_status.json)                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

## ✨ Features

- **Automatic provisioning**: Creates Ampere A1 Flex instances with 4 OCPU, 24 GB RAM, 50 GB boot volume
- **Ubuntu 22.04**: Automatically finds latest compatible image
- **Smart networking**: Auto-creates/reuses VCN, subnet, security list (SSH + ICMP)
- **Multi-AD failover**: Tries AD-1 → AD-2 → AD-3 until capacity available
- **Rate-limit handling**: Exponential backoff (2s → 60s max) with jitter
- **Real-time dashboard**: Flask web UI at `http://localhost:5050` with live logs
- **Detailed logging**: Every attempt tagged with timestamp, AD number, and attempt number
- **Clean shutdown**: Saves instance details to `instance_details.json` on success

## 📋 Prerequisites

1. **Oracle Cloud Account** with access to Frankfurt region (`eu-frankfurt-1`)
2. **OCI API Signing Key** configured (see [OCI Setup](#oci-setup))
3. **Python 3.8+** with `pip`
4. **SSH Key Pair** for instance access

## 🔧 OCI Setup

### 1. Generate API Signing Key

```bash
# Generate RSA key pair
openssl genrsa -out oci_api_key.pem 2048
openssl rsa -pubout -in oci_api_key.pem -out oci_api_key_public.pem
```

### 2. Upload Public Key to OCI Console

1. Open OCI Console → **User menu** → **My Profile** → **API Keys**
2. Click **"Add Public Key"**
3. Paste contents of `oci_api_key_public.pem`
4. Copy the **fingerprint** shown (format: `aa:bb:cc:...`)

### 3. Create OCI Config File

Copy `oci_config.example` to `~/.oci/config` and fill in:

```ini
[DEFAULT]
user=ocid1.user.oc1..<YOUR_USER_OCID>
fingerprint=<YOUR_API_KEY_FINGERPRINT>
tenancy=ocid1.tenancy.oc1..<YOUR_TENANCY_OCID>
region=eu-frankfurt-1
key_file=~/.oci/oci_api_key.pem
```

### 4. Set Private Key Permissions

```bash
chmod 600 ~/.oci/oci_api_key.pem
```

## 🚀 Installation

```bash
# Clone repository
git clone <your-repo-url>
cd oracle-ampere-a1-creator

# Install dependencies
pip install -r requirements.txt
```

## ⚙️ Configuration

Edit `create_ampere_a1.py` to customize:

```python
# Instance configuration
INSTANCE_SHAPE = "VM.Standard.A1.Flex"
OCPU_COUNT = 4
MEMORY_IN_GBS = 24
BOOT_VOLUME_SIZE_IN_GBS = 50
AVAILABILITY_DOMAIN = None  # None = try all ADs, or specify specific AD

# SSH key for INSTANCE ACCESS (different from API signing key)
SSH_PRIVATE_KEY_PEM = """-----BEGIN RSA PRIVATE KEY-----
<YOUR_SSH_PRIVATE_KEY>
-----END RSA PRIVATE KEY-----"""

# Rate limiting
INITIAL_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 60
MAX_RETRIES = 100
BACKOFF_MULTIPLIER = 1.5
JITTER_FACTOR = 0.2

# Max runtime (None = unlimited)
MAX_RUNTIME_SECONDS = None
```

## 🏃 Running

### Option 1: Run both script and dashboard (recommended)

```bash
# Terminal 1: Start dashboard
python dashboard.py

# Terminal 2: Start instance creator
python create_ampere_a1.py
```

Then open **http://localhost:5050** in your browser.

### Option 2: Run script only

```bash
python create_ampere_a1.py
```

### Option 3: One-command startup (Linux/macOS)

```bash
chmod +x run.sh
./run.sh
```

### Option 4: One-command startup (Windows)

```bat
run.bat
```

## 📊 Dashboard

The dashboard at `http://localhost:5050` shows:

- **Status badge**: Trying (blue) / Success (green) / Failed (red)
- **Current availability domain** being attempted
- **Total launch attempts** counter
- **Runtime** elapsed
- **Real-time logs** with timestamp, AD number, and attempt number
- **Instance details** on success (name, OCID, IPs, SSH command)

Logs format:
```
[HH:MM:SS] [AD1] [#3] Server error (500): Out of host capacity., waiting 4.5s...
[HH:MM:SS] [AD1] [#2] Launching instance in gNkw:EU-FRANKFURT-1-AD-1...
[HH:MM:SS] [N/A] OCI clients initialized
```

## 📁 Output Files

| File | Description |
|------|-------------|
| `instance_details.json` | Created on success: instance OCID, IPs, shape, SSH command |
| `ampere_a1_creation.log` | Detailed log file |
| `dashboard_status.json` | Real-time status for dashboard |

## 🔒 Security

- **No secrets in code**: All credentials via `~/.oci/config`
- **API key separate from SSH key**: Different keys for API signing vs instance access
- **Private keys never committed**: Added to `.gitignore`
- **Example config provided**: `oci_config.example` shows format without real values

## 🐛 Troubleshooting

| Error | Solution |
|-------|----------|
| `InvalidKey` / 401 | API signing key not uploaded to OCI Console, or fingerprint mismatch |
| `Out of host capacity` (500) | Normal - script retries with backoff and tries other ADs |
| `Rate limited` (429) | Script handles automatically with exponential backoff |
| `No Ubuntu 22.04 image found` | Check region supports Ampere A1 with Ubuntu 22.04 |
| Dashboard not updating | Ensure `dashboard.py` is running on port 5050 |

## 📝 License

MIT License - feel free to use and modify.

## ⚠️ Disclaimer

This script makes API calls to Oracle Cloud. You are responsible for any charges incurred. The "Out of host capacity" errors are normal when capacity is unavailable - the script handles them gracefully.