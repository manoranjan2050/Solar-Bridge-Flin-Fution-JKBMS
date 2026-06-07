# Run this on your Windows PC to copy files to the Pi and start setup
# Usage: .\transfer_and_run.ps1 -PiIP 192.168.1.50 -PiUser pi

param(
    [Parameter(Mandatory=$true)]
    [string]$PiIP,
    [string]$PiUser = "pi"
)

$files = @("solar_bridge.py", "config.ini", "setup.sh", "solar-bridge.service", "detect_devices.sh")

Write-Host "Copying files to $PiUser@$PiIP ..." -ForegroundColor Cyan

foreach ($f in $files) {
    scp $f "${PiUser}@${PiIP}:~/$f"
}

Write-Host ""
Write-Host "Files copied. Now running setup on the Pi..." -ForegroundColor Cyan
Write-Host ""

ssh "${PiUser}@${PiIP}" "chmod +x ~/setup.sh ~/detect_devices.sh && bash ~/setup.sh"
