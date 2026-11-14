# bench-run.ps1 NAME [run-vphone.ps1 options...]: boot the VM with frame stats and QMP, let iOS
# settle, run winbench.py, then stop the VM. Results: F:\vpw\runs\<NAME>.out (QEMU's output) and
# F:\vpw\runs\<NAME>.bench (the benchmark). Stops any running QEMU first.
param(
    [Parameter(Mandatory)] [string] $Name,
    [string] $Vm = "F:\vpw\vms\duykhanhxx03",
    [int] $SettleMinutes = 13,
    [int] $QmpPort = 4445,
    [Parameter(ValueFromRemainingArguments)] [string[]] $LaunchArgs = @()
)
$ErrorActionPreference = "Stop"
$runs = "F:\vpw\runs"
Get-Process qemu-system-aarch64 -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 3
$env:REIMS_VGPU_STATS = "1"
$out = "$runs\$Name.out"
$launcher = "D:\ios simulator\vphone-win\run-vphone.ps1"
$argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
    "& '$launcher' -Vm '$Vm' -Window -LogDir '$runs\$Name' $($LaunchArgs -join ' ') -- -qmp 'tcp:127.0.0.1:$QmpPort,server=on,wait=off' *> '$out'")
Start-Process powershell -ArgumentList $argList -WindowStyle Hidden | Out-Null
$p = $null
for ($i = 0; $i -lt 60 -and -not $p; $i++) { Start-Sleep 2; $p = Get-Process qemu-system-aarch64 -ErrorAction SilentlyContinue | Select -First 1 }
if (-not $p) { Get-Content $out -Tail 10 -ErrorAction SilentlyContinue; throw "QEMU did not start" }
$deadline = $p.StartTime.AddMinutes($SettleMinutes)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $p.Id -ErrorAction SilentlyContinue)) { Get-Content $out -Tail 10; throw "QEMU exited" }
    Start-Sleep 20
}
$res = & python "D:\ios simulator\vphone-win\tools\winbench.py" --qmp $QmpPort --log $out --pid $p.Id 2>&1
$res | Set-Content "$runs\$Name.bench"
"== $Name ($($LaunchArgs -join ' '))"
$res
