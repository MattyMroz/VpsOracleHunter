# 🎣 VpsOracleHunter

A small, self-contained Windows/PowerShell automation that keeps retrying until
Oracle Cloud frees up capacity, then grabs a **free (Always Free) Ampere A1 VPS**
for you — 2 OCPU / 12 GB RAM / Ubuntu, with a public IPv4.

Oracle's Always Free A1 instances are famously "Out of host capacity" for weeks.
This tool runs quietly in the background and launches the instance the moment a
slot opens up.

## ⚡ What it does

- Polls Oracle Cloud across every Availability Domain in your region.
- Retries on `Out of host capacity` and on transient/unexpected errors — it does
  **not** die on a random hiccup.
- Launches exactly **one** on-demand `VM.Standard.A1.Flex` instance and stops.
- Prints the ready-to-use `ssh` command once the instance is live.
- Touches nothing outside its own folder — no global installs, no services, no tasks.

## 🎯 Target instance

| Parameter     | Value                          |
|---------------|--------------------------------|
| Shape         | `VM.Standard.A1.Flex`          |
| OCPU / RAM    | 2 / 12 GB                      |
| Architecture  | ARM64 (Ampere)                 |
| Image         | Canonical Ubuntu 24.04 (ARM64) |
| Public IPv4   | yes                            |
| Capacity type | on-demand (never preemptible)  |

All targets are configured in [`config/catcher.psd1`](config/catcher.psd1).

## 🛡️ Cost protection

This is Always Free hardware, but a misconfiguration could still create a
billable instance. The catcher refuses to do that:

- Config is hard-validated: only `VM.Standard.A1.Flex` / 2 OCPU / 12 GB is allowed.
- Before the first attempt it sums current A1 usage in the tenancy and aborts if a
  new instance would exceed the Always Free limit (2 OCPU / 12 GB).
- `--preemptible-instance-config` is never passed → capacity type is always on-demand.
- No boot-volume resize, no block volumes, no load balancers — nothing extra is created.

## 📦 Requirements

- Windows + PowerShell 5.1+
- [`uv`](https://docs.astral.sh/uv/) (used to create a local venv and install OCI CLI)
- An Oracle Cloud account with an API key, plus an existing VCN + public subnet

## 🚀 Setup

**1. Install OCI CLI into a local venv** (stays inside the project, never on PATH):

```powershell
uv venv .venv
uv pip install --python .venv oci-cli
```

**2. Drop your credentials into `.secrets\`** (git-ignored, never committed):

```text
.secrets\oci\config            # OCI config (points at the .pem below)
.secrets\oci\oci_api_key.pem   # your OCI API private key
.secrets\ssh\main-vps          # SSH private key for the VM
.secrets\ssh\main-vps.pub      # SSH public key sent to the VM
```

**3. Resolve your OCI resources** into `var\runtime\resolved.json`:

```json
{ "CompartmentId": "ocid1.tenancy.oc1..xxxx", "SubnetId": "ocid1.subnet.oc1..xxxx" }
```

**4. Review the target** in `config\catcher.psd1` (region, VM name, retry interval).

## ▶️ Usage

```powershell
.\scripts\start.ps1      # start in the background
.\scripts\status.ps1     # show catcher + live OCI instance state
.\scripts\stop.ps1       # stop (never touches OCI resources)
```

Follow the log live:

```powershell
Get-Content .\var\logs\catcher.log -Wait -Tail 30
```

> The catcher does **not** auto-start after a reboot — this is intentional.
> Run `scripts\start.ps1` again.

## 🔁 How the loop works

1. **Preflight** — list ADs, resolve the latest Ubuntu 24.04 ARM64 image, check the
   Always Free limit.
2. **Round** — try AD-1, then AD-2, then AD-3.
3. `Out of host capacity` → next AD; after a full round → pause and repeat.
4. **Transient / unexpected error** → logged and retried with exponential backoff,
   the automat keeps running.
5. **Hard error** (`LimitExceeded`, bad credentials, invalid parameter, …) → stop
   with full diagnostics. These need a human, so retrying is pointless.
6. **Success** → write `var\runtime\success.json`, print the instance details and
   `ssh` command, then exit.

## 📁 Layout

```text
VpsOracleHunter\
├── .venv\                 # local Python + OCI CLI (git-ignored)
├── .secrets\              # OCI + SSH credentials (git-ignored, NEVER commit)
├── scripts\
│   ├── catch-a1.ps1       # the catcher
│   ├── start.ps1          # run in background
│   ├── status.ps1         # catcher + OCI instance state
│   └── stop.ps1           # stop (local process only)
├── config\catcher.psd1    # non-secret parameters
└── var\                   # logs + runtime state (git-ignored)
```

## 🔒 Security

- `.secrets\`, `.venv\` and `var\` are git-ignored — no keys or state ever leave the machine.
- The log stores only timestamps, round numbers, ADs, results and error codes — never keys or tokens.
- Only the contents of `.secrets\ssh\main-vps.pub` are ever sent to the VM.
