# Windows twin of the Makefile:  .\tasks.ps1 relay | laptop-mock | laptop-real | fake-events | tap | test | schemas | mock-scene | check | golden
#   demo stack (tools/demo.py):  .\tasks.ps1 demo-up [--source real|replay|fake|none] [--cloud] | demo-reset [--full] | demo-down | demo-status | demo-check | latency [--relay wss://host/ws]
param([Parameter(Mandatory = $true)][string]$Task, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $PSScriptRoot "..\shh\.venv\Scripts\python.exe" }   # worktrees share the main venv
if (-not (Test-Path $py)) { $py = "py" }
Push-Location $PSScriptRoot
try {
    switch ($Task) {
        "relay"       { & $py -m relay --host 0.0.0.0 --port 8080 }
        "laptop-mock" { & $py -m laptop --mock }
        "laptop-real" { & $py -m laptop --real }
        "fake-events" { & $py tools/fake_events.py --loop }
        "tap"         { & $py tools/relay_tap.py --validate }
        "test"        { & $py -m pytest contracts relay laptop tests extras -q }
        "schemas"     { & $py contracts/build_schemas.py }
        "mock-scene"  { & $py tools/make_mock_scene.py }
        "check"       { & $py tools/check_gpu.py }
        "golden"      { & $py tools/stress_test.py --golden; exit $LASTEXITCODE }
        "demo-up"     { & $py tools/demo.py up @Rest }
        "demo-reset"  { & $py tools/demo.py reset @Rest }
        "demo-down"   { & $py tools/demo.py down @Rest }
        "demo-status" { & $py tools/demo.py status @Rest }
        "latency"     { & $py tools/latency_probe.py @Rest }
        "demo-check"  { & $py tools/check_gpu.py --demo; exit $LASTEXITCODE }
        default       { Write-Host "unknown task '$Task'"; exit 1 }
    }
} finally { Pop-Location }
