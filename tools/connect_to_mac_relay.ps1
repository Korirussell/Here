# Point the Windows laptop at the relay now running on the Mac (relay/MAC_RELAY.md).
# The Windows laptop can't accept inbound connections (CrowdStrike), so it connects OUT to the Mac,
# same as it would to Vultr.
#
# Usage (from the repo root, in PowerShell):
#   .\tools\connect_to_mac_relay.ps1
#   .\tools\connect_to_mac_relay.ps1 -MacIp 172.20.10.4      # if the hotspot handed out a different IP
#
# If this says UNREACHABLE: confirm the Mac, laptop, and phone are all on the same hotspot/network,
# and that `curl http://<MacIp>:8080/health` answers from this laptop first.

param(
    [string]$MacIp = "10.37.50.184"
)

$env:HERE_RELAY_URL = "ws://${MacIp}:8080/ws"
Write-Host "Using relay: $env:HERE_RELAY_URL"

.venv\Scripts\python.exe tools\demo.py down
.venv\Scripts\python.exe tools\demo.py up --source real --cloud
.venv\Scripts\python.exe tools\demo.py status --cloud
