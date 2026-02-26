<#
.SYNOPSIS
    OSC Replay (PowerShell) — Replays a recorded .osc file, sending packets via UDP.
    No dependencies. Runs on any Windows with PowerShell 5.1+.

.DESCRIPTION
    Reads a binary .osc recording and sends each packet to a target host/port,
    respecting original timing. Loops indefinitely by default.
    Press Ctrl+C to stop.

.PARAMETER File
    Path to the .osc recording file (default: recording.osc)

.PARAMETER Port
    Target UDP port (default: 9000)

.PARAMETER Host
    Target host (default: 127.0.0.1)

.PARAMETER Loop
    Loop playback indefinitely (default: true)

.PARAMETER Speed
    Playback speed factor (default: 1.0, 2.0 = twice as fast)

.EXAMPLE
    .\replay.ps1
    .\replay.ps1 -File my_session.osc -Port 8000 -Speed 2
#>

param(
    [string]$File = "recording.osc",
    [int]$Port = 9000,
    [string]$HostAddr = "127.0.0.1",
    [switch]$NoLoop,
    [double]$Speed = 1.0
)

$ErrorActionPreference = "Stop"

$HEADER_MAGIC = [System.Text.Encoding]::ASCII.GetBytes("OSCREC01")

function Get-OscAddress([byte[]]$data) {
    $end = [Array]::IndexOf($data, [byte]0)
    if ($end -lt 0) { return "???" }
    try {
        return [System.Text.Encoding]::ASCII.GetString($data, 0, $end)
    } catch {
        return "???"
    }
}

# --- Load recording ---
$filePath = [System.IO.Path]::GetFullPath($File)
if (-not (Test-Path $filePath)) {
    Write-Host "[ERROR] File not found: $filePath"
    exit 1
}

$fs = [System.IO.File]::OpenRead($filePath)
try {
    # Validate header
    $header = New-Object byte[] 8
    $bytesRead = $fs.Read($header, 0, 8)
    if ($bytesRead -lt 8 -or ([System.Text.Encoding]::ASCII.GetString($header) -ne "OSCREC01")) {
        Write-Host "[ERROR] Invalid file header. Expected OSCREC01."
        exit 1
    }

    $packets = [System.Collections.Generic.List[object]]::new()
    $tsBuf  = New-Object byte[] 8
    $lenBuf = New-Object byte[] 4

    while ($true) {
        if ($fs.Read($tsBuf, 0, 8) -lt 8) { break }
        if ($fs.Read($lenBuf, 0, 4) -lt 4) { break }

        $ts     = [BitConverter]::ToDouble($tsBuf, 0)
        $length = [BitConverter]::ToUInt32($lenBuf, 0)

        $data = New-Object byte[] $length
        if ($fs.Read($data, 0, $length) -lt $length) { break }

        $packets.Add(@{ Time = $ts; Data = $data })
    }
}
finally {
    $fs.Close()
}

if ($packets.Count -eq 0) {
    Write-Host "[ERROR] No packets in recording."
    exit 1
}

$duration = $packets[$packets.Count - 1].Time
$doLoop = -not $NoLoop
Write-Host "[OSC Replay] Loaded $($packets.Count) packets ($([math]::Round($duration, 2))s) from $File"
Write-Host "[OSC Replay] Sending to ${HostAddr}:${Port}  speed=${Speed}x  loop=$doLoop"
Write-Host "[OSC Replay] Press Ctrl+C to stop."
Write-Host ""

# --- Setup UDP sender ---
$udpClient = New-Object System.Net.Sockets.UdpClient
$loopCount = 0

try {
    while ($true) {
        $loopCount++
        if ($doLoop) {
            Write-Host "--- Loop #$loopCount ---"
        }

        $sw = [System.Diagnostics.Stopwatch]::StartNew()

        for ($i = 0; $i -lt $packets.Count; $i++) {
            $pkt = $packets[$i]
            $adjustedTs = $pkt.Time / $Speed

            # Wait until correct relative time
            $targetMs = $adjustedTs * 1000
            $elapsed  = $sw.Elapsed.TotalMilliseconds
            $waitMs   = $targetMs - $elapsed
            if ($waitMs -gt 0) {
                [System.Threading.Thread]::Sleep([int][math]::Min($waitMs, [int]::MaxValue))
            }

            [void]$udpClient.Send($pkt.Data, $pkt.Data.Length, $HostAddr, $Port)

            $addr = Get-OscAddress $pkt.Data
            $idx  = $i + 1
            Write-Host ("  [{0,8:F3}s] #{1,-5}  {2}  ({3} bytes)" -f $pkt.Time, $idx, $addr, $pkt.Data.Length)
        }

        if (-not $doLoop) { break }
    }
}
catch {
    # Ctrl+C
}
finally {
    $udpClient.Close()
}

Write-Host ""
Write-Host "[OSC Replay] Stopped after $loopCount loop(s). Done."
