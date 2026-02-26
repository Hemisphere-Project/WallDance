<#
.SYNOPSIS
    OSC Recorder (PowerShell) — Captures incoming OSC (UDP) packets with timestamps.
    No dependencies. Runs on any Windows with PowerShell 5.1+.

.DESCRIPTION
    Listens on a UDP port, records raw packets with relative timestamps to a binary file.
    Press Ctrl+C to stop and save.

.PARAMETER Port
    UDP port to listen on (default: 9000)

.PARAMETER Output
    Output file path (default: recording.osc)

.EXAMPLE
    .\record.ps1
    .\record.ps1 -Port 8000 -Output my_session.osc
#>

param(
    [int]$Port = 9000,
    [string]$Output = "recording.osc"
)

$ErrorActionPreference = "Stop"

# File header
$HEADER_MAGIC = [System.Text.Encoding]::ASCII.GetBytes("OSCREC01")

function Get-OscAddress([byte[]]$data) {
    # OSC address is a null-terminated ASCII string at the start of the packet
    $end = [Array]::IndexOf($data, [byte]0)
    if ($end -lt 0) { return "???" }
    try {
        return [System.Text.Encoding]::ASCII.GetString($data, 0, $end)
    } catch {
        return "???"
    }
}

# --- Setup UDP listener ---
$udpClient = New-Object System.Net.Sockets.UdpClient($Port)
$udpClient.Client.ReceiveTimeout = 500  # ms, allows Ctrl+C checking

$outputPath = [System.IO.Path]::GetFullPath($Output)

Write-Host "[OSC Record] Listening on UDP port $Port"
Write-Host "[OSC Record] Recording to: $outputPath"
Write-Host "[OSC Record] Press Ctrl+C to stop and save."
Write-Host ""

$packets = [System.Collections.Generic.List[object]]::new()
$count = 0
$startTime = $null
$stopwatch = [System.Diagnostics.Stopwatch]::new()
$remoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)

# Trap Ctrl+C to allow graceful save
[Console]::TreatControlCAsInput = $false
$running = $true

try {
    while ($running) {
        try {
            $data = $udpClient.Receive([ref]$remoteEP)
        }
        catch [System.Net.Sockets.SocketException] {
            # Timeout — loop back to check for Ctrl+C
            continue
        }

        if ($null -eq $startTime) {
            $startTime = [System.Diagnostics.Stopwatch]::GetTimestamp()
            $stopwatch.Start()
        }

        $relSeconds = $stopwatch.Elapsed.TotalSeconds
        $packets.Add(@{ Time = $relSeconds; Data = $data })
        $count++

        $addr = Get-OscAddress $data
        $source = "$($remoteEP.Address):$($remoteEP.Port)"
        Write-Host ("  [{0,8:F3}s] #{1,-5}  {2}  ({3} bytes) from {4}" -f $relSeconds, $count, $addr, $data.Length, $source)
    }
}
catch {
    # Ctrl+C or other interruption
}
finally {
    $udpClient.Close()
}

Write-Host ""
Write-Host "[OSC Record] Stopped. $count packet(s) captured."

if ($count -eq 0) {
    Write-Host "[OSC Record] Nothing to save."
    exit 0
}

# --- Write binary file ---
$fs = [System.IO.File]::Create($outputPath)
try {
    $fs.Write($HEADER_MAGIC, 0, $HEADER_MAGIC.Length)

    foreach ($pkt in $packets) {
        $tsBytes  = [BitConverter]::GetBytes([double]$pkt.Time)    # 8 bytes LE
        $lenBytes = [BitConverter]::GetBytes([uint32]$pkt.Data.Length)  # 4 bytes LE
        $fs.Write($tsBytes,  0, 8)
        $fs.Write($lenBytes, 0, 4)
        $fs.Write($pkt.Data, 0, $pkt.Data.Length)
    }
}
finally {
    $fs.Close()
}

$size = (Get-Item $outputPath).Length
Write-Host "[OSC Record] Saved $count packets to $Output ($size bytes)"
