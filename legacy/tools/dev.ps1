# Start the whole laptop pipeline with stand-ins, each in its own window.
#   .\tools\dev.ps1                 # hub + webcam + mic + fake DoA + vision + speech + sound + debug view
#   .\tools\dev.ps1 -NoMic          # use a looping test WAV instead of the microphone
#   .\tools\dev.ps1 -Hfov 78        # your webcam's measured horizontal FOV
#   .\tools\dev.ps1 -ReSpeaker      # the real array (audio_here) instead of fake audio + fake DoA
param([switch]$NoMic, [switch]$ReSpeaker, [string]$Wav = "", [double]$Hfov = 70)

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = (Get-Command python).Source }

function Start-Part($title, $cmd) {
    Start-Process powershell -WorkingDirectory $root -ArgumentList @(
        "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle = '$title'; & '$py' $cmd")
}

Start-Part "world_here (hub)" "-m world_here"
Start-Sleep -Seconds 2
Start-Part "vision_here" "-m vision_here"
Start-Part "speech_here" "-m speech_here"
Start-Part "sound_here" "-m sound_here"
Start-Part "fake phone (webcam)" "tools\fake_phone.py --hfov $Hfov"
if ($ReSpeaker) {
    Start-Part "audio_here (ReSpeaker)" "-m audio_here"
} elseif ($NoMic) {
    if (-not $Wav) { $Wav = Join-Path $env:TEMP "here_test.wav" }
    Start-Part "fake audio (wav)" "tools\fake_audio.py --wav `"$Wav`""
} else {
    Start-Part "fake audio (mic)" "tools\fake_audio.py"
}
if (-not $ReSpeaker) { Start-Part "fake DoA" "tools\fake_doa.py" }
Start-Part "debug view" "tools\debug_view.py"
