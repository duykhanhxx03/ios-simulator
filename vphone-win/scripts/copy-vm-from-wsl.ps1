<#
.SYNOPSIS
  Copy the files a vphone VM boots from (a WSL bundle made by vphone-linux's create-vm.sh) to a
  Windows folder for run-vphone.ps1, keeping Disk.img sparse on NTFS.

.DESCRIPTION
  Copies only the boot inputs (bundle-info.py lists them): config.plist, ecid, the two ROMs named
  by config.plist's romImages, nvram.bin, Disk.img, SEPStorage, and serial / gfx-res / gfx-res.3x
  / cid when present. The restore tree, FirmwareOriginals, patch plists and run logs stay behind.

  Disk.img (64 GB apparent, ~17 GB data) is copied sparse: the destination is created here,
  marked sparse (fsutil sparse setflag) and given the full length, then sparse-copy.py inside WSL
  writes only the source's data extents into it through /mnt/<drive> (skipping all-zero chunks).
  A plain `cp --sparse=always` onto /mnt/<drive> is NOT enough: drvfs cannot set the NTFS sparse
  attribute, and the file comes out fully allocated (checked: 1 GiB with 5 MiB of data -> 1 GiB).

  Refuses while a qemu-system-aarch64 in WSL names the bundle, or any WSL process has a file in it
  open. Checks the destination drive's free space against the files' allocated (not apparent)
  sizes. Without -Dest, uses D:\vpw\vms\<name> when it fits, else F:\vpw\vms\<name>.

.EXAMPLE
  .\copy-vm-from-wsl.ps1 -Plan          # checks + what would be copied, copies nothing
  .\copy-vm-from-wsl.ps1                # copy duykhanhxx03 to D: (or F: if D: is too full)
  .\copy-vm-from-wsl.ps1 -Dest F:\vms\iphone -Verify
#>
param(
    [string] $Distro = "vphone",
    [string] $Bundle = "/home/vphone/vphone-setup-work/devices/duykhanhxx03",
    [string] $Dest = "",
    [string[]] $FallbackRoots = @("D:\vpw\vms", "F:\vpw\vms"),
    [long] $MarginBytes = 2GB,   # left free on the destination drive after the copy
    [switch] $Plan,              # check and print, copy nothing
    [switch] $Force,             # overwrite an existing destination Disk.img
    [switch] $Verify             # cmp every copied file afterwards (reads 64 GB apparent: slow)
)
$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "copy-vm-from-wsl: $msg" -ForegroundColor Red; exit 1 }

# Windows path -> its drvfs path in WSL (D:\a b\c -> /mnt/d/a b/c)
function ConvertTo-WslPath([string] $p) {
    $full = [IO.Path]::GetFullPath($p)
    if ($full -notmatch '^([A-Za-z]):\\?(.*)$') { Fail "$p is not on a drive letter" }
    "/mnt/" + $Matches[1].ToLower() + "/" + ($Matches[2] -replace '\\', '/')
}

function Invoke-Wsl([string[]] $argv, [switch] $Root) {
    $u = if ($Root) { @("-u", "root") } else { @() }
    # PowerShell 5.1 turns a native command's stderr (the copy's progress lines) into
    # terminating errors under Stop when stderr is redirected; the exit code is checked below.
    $ErrorActionPreference = "Continue"
    $out = & wsl.exe -d $Distro @u -e @argv
    if ($LASTEXITCODE -ne 0) { Fail "WSL command failed ($LASTEXITCODE): $($argv -join ' ')" }
    $out
}

function Get-FreeBytes([string] $path) {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
    (New-Object IO.DriveInfo $root).AvailableFreeSpace
}

Add-Type -Namespace VphoneCopy -Name Native -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern uint GetCompressedFileSizeW(string name, out uint high);
'@
function Get-AllocatedBytes([string] $path) {
    $hi = [uint32]0
    $lo = [VphoneCopy.Native]::GetCompressedFileSizeW($path, [ref] $hi)
    ([uint64]$hi -shl 32) + $lo
}

$scripts = ConvertTo-WslPath $PSScriptRoot

# 1. What the bundle holds, and who has it open (as root: every process is visible)
$info = (Invoke-Wsl @("python3", "$scripts/bundle-info.py", $Bundle) -Root) -join "`n" | ConvertFrom-Json
if ($info.holders.Count -gt 0) {
    Write-Host "The bundle is in use inside WSL:" -ForegroundColor Red
    foreach ($h in $info.holders) { Write-Host ("  pid {0} {1}: {2}" -f $h.pid, $h.comm, ($h.why -join ", ")) }
    Fail "stop the VM (and create-vm.sh) first; copying a disk a guest is writing gives a torn image."
}
$missing = @($info.files | Where-Object { $_.required -and -not $_.exists } | ForEach-Object name)
if ($missing.Count) { Fail "the bundle lacks $($missing -join ', ')" }
$files = @($info.files | Where-Object exists)
$need = [long](($files | Measure-Object allocated -Sum).Sum)
$apparent = [long](($files | Measure-Object size -Sum).Sum)

# 2. Where to, with room for the allocated bytes plus a margin
$name = Split-Path $info.vm -Leaf
if ($Dest) {
    $free = Get-FreeBytes $Dest
    if ($free -lt $need + $MarginBytes) {
        Fail ("{0} has {1:N1} GB free; the copy needs {2:N1} GB + {3:N1} GB margin" -f $Dest, ($free/1GB), ($need/1GB), ($MarginBytes/1GB))
    }
} else {
    foreach ($r in $FallbackRoots) {
        if (-not (Test-Path ([IO.Path]::GetPathRoot($r)))) { continue }
        $free = Get-FreeBytes $r
        if ($free -ge $need + $MarginBytes) { $Dest = Join-Path $r $name; break }
        Write-Host ("{0}: {1:N1} GB free, not enough for {2:N1} GB + {3:N1} GB margin" -f $r, ($free/1GB), ($need/1GB), ($MarginBytes/1GB)) -ForegroundColor Yellow
    }
    if (-not $Dest) { Fail "no destination in $($FallbackRoots -join ', ') has room" }
}
$Dest = [IO.Path]::GetFullPath($Dest)
$destWsl = ConvertTo-WslPath $Dest

Write-Host ("bundle:      {0} ({1})" -f $info.vm, $Distro)
Write-Host ("destination: {0}" -f $Dest)
Write-Host ("to copy:     {0:N2} GB allocated ({1:N1} GB apparent), {2:N1} GB free there" -f ($need/1GB), ($apparent/1GB), ((Get-FreeBytes $Dest)/1GB))
foreach ($f in $files) { Write-Host ("  {0,-14} {1,16:N0} bytes, {2,16:N0} allocated" -f $f.name, $f.size, $f.allocated) }
$skipped = @($info.files | Where-Object { -not $_.exists } | ForEach-Object name)
if ($skipped.Count) { Write-Host "  (absent, optional: $($skipped -join ', '))" }
if ($Plan) { Write-Host "-Plan: nothing copied."; exit 0 }

$destDisk = Join-Path $Dest "Disk.img"
if ((Test-Path $destDisk) -and -not $Force) { Fail "$destDisk exists; -Force to overwrite" }
New-Item -ItemType Directory -Force $Dest | Out-Null

# 3. The small files through drvfs (cp keeps the bytes; sparseness does not matter for them)
foreach ($f in $files | Where-Object name -ne "Disk.img") {
    Invoke-Wsl @("cp", "--", "$($info.vm)/$($f.name)", "$destWsl/$($f.name)") | Out-Null
}

# 4. Disk.img: sparse NTFS file of the full length, then only the data written into it
$disk = $files | Where-Object name -eq "Disk.img"
if (Test-Path $destDisk) { Remove-Item -Force $destDisk }
New-Item -ItemType File $destDisk | Out-Null
fsutil sparse setflag $destDisk | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "fsutil sparse setflag failed on $destDisk (not NTFS?)" }
$fs = [IO.File]::Open($destDisk, 'Open', 'ReadWrite')
try { $fs.SetLength([long]$disk.size) } finally { $fs.Close() }
Write-Host ("copying Disk.img's data ({0:N0} MiB)..." -f ($disk.allocated/1MB))
$t0 = Get-Date
$result = Invoke-Wsl @("python3", "$scripts/sparse-copy.py", "$($info.vm)/Disk.img", "$destWsl/Disk.img")
Write-Host "sparse-copy: $result  ($([int]((Get-Date) - $t0).TotalSeconds) s)"

# 5. Check: same lengths, Disk.img still sparse and no bigger on disk than its data
foreach ($f in $files) {
    $len = (Get-Item (Join-Path $Dest $f.name)).Length
    if ($len -ne [long]$f.size) { Fail "$($f.name): $len bytes copied, the source has $($f.size)" }
}
$flag = fsutil sparse queryflag $destDisk
$alloc = Get-AllocatedBytes $destDisk
Write-Host ("Disk.img on NTFS: {0:N0} bytes, {1:N0} MiB allocated; {2}" -f (Get-Item $destDisk).Length, ($alloc/1MB), ($flag -join ' '))
if ($alloc -gt [long]$disk.allocated + 64MB) { Write-Host "warning: Disk.img took more space than its data" -ForegroundColor Yellow }
if ($Verify) {
    foreach ($f in $files) {
        Write-Host "cmp $($f.name)..."
        Invoke-Wsl @("cmp", "--", "$($info.vm)/$($f.name)", "$destWsl/$($f.name)") | Out-Null
    }
    Write-Host "verified: every file matches."
}
Write-Host "done: .\run-vphone.ps1 -Vm `"$Dest`""
