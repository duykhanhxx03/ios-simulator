<#
.SYNOPSIS
  Boot a vphone VM bundle (made by vphone-linux's create-vm.sh) natively on Windows, with the
  guest's Metal drawn by the host GPU through reims-vgpu on Vulkan (the RTX's driver, not lavapipe).

.DESCRIPTION
  The Windows counterpart of vphone-linux's run-vphone.sh; the QEMU command line is the same
  argument for argument, except:
  - guest RAM is memory-backend-section (a pagefile-backed section reims-vgpu maps views of),
    where Linux uses memory-backend-file on /dev/shm,share=on,align=2M. The section backend
    always maps at a 2 MiB-aligned address and has no align property; share=on is kept (the
    property exists; the section is shared with reims-vgpu by its handle either way).
    The /dev/shm free-space check becomes a check of free commit (the section is SEC_COMMIT).
  - vsock is virtio-vsock-sock-pci (the datapath in QEMU; vhost-vsock is Linux-only), its host
    side a TCP listener on 127.0.0.1:-VsockPort (default 13390, 0 = no vsock) speaking the
    Firecracker "CONNECT <port>\n" / "OK <n>\n" protocol of VSOCK=uds; tools\vphoned.py talks
    to vphoned through it. The guest CID is <vm>\cid (default 3), as CID on Linux.
    No vphone-window.py button bar (it talks to the window over a Unix socket and to vphoned
    over AF_VSOCK). REIMS_VGPU_WINDOW_CONTROL is not set.
  - no VK_DRIVER_FILES / LP_NUM_THREADS selection: the Vulkan loader's default on Windows is the
    NVIDIA ICD from the registry, which has VK_EXT_external_memory_host. A VK_DRIVER_FILES already
    in the environment is passed through (with a warning), as run-vphone.sh does.
  - the serial console is `-serial mon:stdio` written out as its chardev (stdio, mux=on) plus a
    logfile=, so the console is interactive as on Linux and also lands in <log>\serial.log.
  - GFX is on by default (Linux: GFX=0 unless set); -NoGfx turns it off.
  - VPHONE_CPUS (taskset) becomes -Cpus: a list like 0-11 or 0,2,4 set as this process's affinity,
    which QEMU inherits. The default leaves placement to the scheduler (an i5-12400F has no E-cores;
    run-vphone.sh only pins when /sys/devices/cpu_core exists, i.e. on hybrid CPUs).
  Every env var run-vphone.sh exports is set here with the same default, and, like ${X:-default},
  a value already in the environment wins.

.EXAMPLE
  .\run-vphone.ps1 -Vm D:\vpw\vms\duykhanhxx03 -Window
  .\run-vphone.ps1 -Vm D:\vpw\vms\duykhanhxx03 -DryRun
  .\run-vphone.ps1 -Vm D:\vpw\vms\duykhanhxx03 -Gdb 1234 -- -d guest_errors
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [string] $Vm,
    [int] $Smp = 6,                     # SMP (Linux: 8). 6 = one vCPU per core of a 6-core host:
                                        # measured 21.8 -> 35.8 fps scrolling Settings vs 8 (i5-12400F)
    [string] $Mem = "8G",               # MEM
    [string] $GfxRes = "",              # GFX_RES (default: <vm>\gfx-res, else 1290x2796)
    [string] $GfxModes = "",            # GFX_MODES (default: <GfxRes>@60)
    [int] $GfxPpi = 0,                  # GFX_PPI (opt-in: -global apple-gfx-mmio.pixels-per-inch)
    [ValidateSet("on", "off")] [string] $GfxIsolation = "on",  # GFX_ISOLATION
    [switch] $Window,                   # WINDOW=1
    [switch] $NoGfx,                    # GFX=0
    [int] $TbSize = 2048,               # TB_SIZE
    [string] $Log = "",                 # LOG (-D file)
    [string] $DFlags = "",              # DFLAGS (-d flags)
    [string] $Trace = "",               # TRACE (-trace pattern)
    [int] $Gdb = 0,                     # GDB=<port>: wait for gdb on 127.0.0.1:<port>
    [string[]] $Cpus = @(),              # VPHONE_CPUS (e.g. 0-11)
    [int] $MonitorPort = 0,             # extra HMP monitor on tcp 127.0.0.1:<port> (0 = none)
    [int] $VsockPort = 13390,           # vsock host side on tcp 127.0.0.1:<port> (0 = no vsock; CID=0)
    [switch] $NoCensus,                 # REIMS_VGPU_CENSUS_DIR= (empty): degradations kill the VM
    [switch] $KeepVkLayers,             # keep other programs' implicit Vulkan layers (overlays, capture hooks)
    [string] $Qemu = "D:\vpw\build\qemu-pvg\qemu-system-aarch64.exe",
    [string] $LogDir = "",              # default: <script>\runs\<timestamp>
    [switch] $DryRun,                   # print the command line and environment, run nothing
    [Parameter(ValueFromRemainingArguments)] [string[]] $ExtraArgs = @()  # "$@"
)
$ErrorActionPreference = "Stop"

# QEMU's DLLs (glib, SDL, pixman, nettle...) are MSYS2 UCRT64's.
$env:PATH = "D:\msys64\ucrt64\bin;$env:PATH"

# QEMU option values separate on ','; a literal comma in a path is ',,'.
function Esc([string] $s) { $s -replace ',', ',,' }

# ${NAME:-default}: keep a value already in the environment.
$envSet = [ordered]@{}
function Set-EnvDefault([string] $name, [string] $value) {
    $cur = [Environment]::GetEnvironmentVariable($name, "Process")
    if (-not $cur) { $cur = $value; [Environment]::SetEnvironmentVariable($name, $value, "Process") }
    $envSet[$name] = $cur
}

function ConvertFrom-Size([string] $s) {
    if ($s -notmatch '^(\d+)([KMGT]?)i?B?$') { throw "run-vphone.ps1: cannot read memory size '$s'" }
    $mult = @{ "" = 1MB; "K" = 1KB; "M" = 1MB; "G" = 1GB; "T" = 1TB }   # bare number: MiB, as QEMU -m
    [long]$Matches[1] * $mult[$Matches[2].ToUpper()]
}

function Read-VmFile([string] $name) {
    $p = Join-Path $Vm $name
    if (Test-Path $p) { return ((Get-Content $p -Raw) -replace '\s', '') }
    if ($DryRun) { return $null }
    throw "run-vphone.ps1: $p is missing"
}

$Vm = if (Test-Path $Vm) { (Resolve-Path $Vm).Path } elseif ($DryRun) { [IO.Path]::GetFullPath($Vm) } else { throw "run-vphone.ps1: no VM folder $Vm" }
if ($DryRun -and -not (Test-Path $Vm)) { Write-Host "(dry run: $Vm does not exist; placeholders below)" -ForegroundColor Yellow }
$ecid = Read-VmFile "ecid"
if (-not $ecid) { $ecid = "<ecid>" }

# The ROM files a machine boots: config.plist's romImages (OpenAVB/OpenSEP for machines made by
# vphone-cli 2.10+) or Apple's Virtualization.framework names.
$romName = "AVPBooter.vresearch1.bin"; $sepName = "AVPSEPBooter.vresearch1.bin"
if (Test-Path "$Vm\config.plist") {
    $py = @'
import plistlib, sys
r = (plistlib.load(open(sys.argv[1], 'rb')).get('romImages') or {})
print(r.get('avpBooter', '') or '-'); print(r.get('avpSEPBooter', '') or '-')
'@
    $names = @()
    foreach ($exe in @("python", "py")) {
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            try { $names = @($py | & $exe - "$Vm\config.plist") } catch { $names = @() }
            if ($LASTEXITCODE -eq 0 -and $names.Count -ge 2) { break }
            $names = @()
        }
    }
    if ($names.Count -ge 2) {
        if ($names[0] -ne '-') { $romName = $names[0].Trim() }
        if ($names[1] -ne '-') { $sepName = $names[1].Trim() }
    } else {
        # No Python: the plist is XML; take the two keys' strings directly.
        $x = Get-Content "$Vm\config.plist" -Raw
        if ($x -match '<key>avpBooter</key>\s*<string>([^<]+)</string>') { $romName = $Matches[1] }
        if ($x -match '<key>avpSEPBooter</key>\s*<string>([^<]+)</string>') { $sepName = $Matches[1] }
    }
}

$machine = "vresearch101,ecid=$ecid,sep-rom=$(Esc "$Vm\$sepName")"
# the serial number Virtualization.framework reported for this machine, if known
if (Test-Path "$Vm\serial") {
    $serial = Read-VmFile "serial"
    if ($serial) { $machine += ",serial=$serial" }
}

$runDir = if ($LogDir) { $LogDir } else { "$PSScriptRoot\runs\$(Get-Date -Format yyyyMMdd-HHmmss)" }

$qargs = @(
    "-accel", "tcg,tb-size=$TbSize",
    "-smp", "$Smp,maxcpus=$($Smp + 1)", "-m", $Mem,
    "-bios", "$Vm\$romName",
    # nvram.bin is Virtualization.framework aux storage: a 16 KiB header, then the firmware store
    "-drive", "file=$(Esc "$Vm\nvram.bin"),if=pflash,format=raw,offset=16384,file.locking=off",
    "-drive", "file=$(Esc "$Vm\Disk.img"),if=pflash,format=raw,file.locking=off",
    # the root APFS disk as an Apple VirtIO PCI storage device too
    "-drive", "file=$(Esc "$Vm\Disk.img"),if=none,id=root-pci,format=raw,file.locking=off",
    # the aux storage as Virtualization.framework's "aux" virtio-blk (RestoreOS's IONVRAM)
    "-drive", "file=$(Esc "$Vm\nvram.bin"),if=none,id=aux-pci,format=raw,offset=16384,file.locking=off",
    # = -serial mon:stdio, plus a copy of the console in serial.log
    "-chardev", "stdio,id=con0,mux=on,logfile=$(Esc "$runDir\serial.log"),logappend=on",
    "-serial", "chardev:con0", "-mon", "chardev=con0"
)
# The SEP coprocessor's persistent storage; sepOS's SEPD reads it at boot
if ((Test-Path "$Vm\SEPStorage") -or ($DryRun -and -not (Test-Path $Vm))) {
    $qargs += @("-drive", "file=$(Esc "$Vm\SEPStorage"),if=none,id=sep-storage,format=raw,file.locking=off")
}

$memBytes = ConvertFrom-Size $Mem
if (-not $NoGfx) {
    if (-not $GfxRes) {
        $GfxRes = Read-VmFile "gfx-res"
        if (-not $GfxRes) { $GfxRes = "1290x2796" }
    }
    if (-not $GfxModes) { $GfxModes = "$GfxRes@60" }
    $machine += ",gfx-device=apple-gfx-mmio,memory-backend=ram0,gfx-modes=$GfxModes"
    if ($GfxPpi) {
        if ($GfxPpi -lt 1 -or $GfxPpi -gt 65535) { throw "run-vphone.ps1: -GfxPpi must be an integer from 1 to 65535" }
        # Not in this build's apple-gfx-mmio (QEMU rejects it): opt-in, as on Linux.
        $qargs += @("-global", "apple-gfx-mmio.pixels-per-inch=$GfxPpi")
    }
    # The section is committed memory: Windows must have the guest's RAM free in commit
    # (RAM + pagefile), as Linux checks /dev/shm. Without it QEMU fails creating the section.
    $os = Get-CimInstance Win32_OperatingSystem
    $freeCommit = [long]$os.FreeVirtualMemory * 1KB
    if ($freeCommit -lt $memBytes) {
        $msg = "run-vphone.ps1: {0:N1} GB of commit is free, the guest's RAM needs {1}. Close other VMs (wsl --shutdown) or enlarge the pagefile." -f ($freeCommit / 1GB), $Mem
        if ($DryRun) { Write-Host $msg -ForegroundColor Yellow } else { throw $msg }
    }
    $qargs += @(
        "-object", "memory-backend-section,id=ram0,size=$Mem,share=on",
        "-global", "apple-gfx-mmio.resolution=$GfxRes",
        "-global", "apple-gfx-mmio.process-isolation=$GfxIsolation"
    )
    # REIMS_VGPU_WINDOW=${WINDOW:-0}: set from -Window, not inherited
    $env:REIMS_VGPU_WINDOW = if ($Window) { "1" } else { "0" }
    $envSet["REIMS_VGPU_WINDOW"] = $env:REIMS_VGPU_WINDOW
    Set-EnvDefault "REIMS_VGPU_WINDOW_TITLE" "vphone-win"
    # Degradations are recorded in census-<pid>.json and relayed instead of killing the VM.
    if ($NoCensus) {
        Remove-Item Env:REIMS_VGPU_CENSUS_DIR -ErrorAction SilentlyContinue
    } else {
        Set-EnvDefault "REIMS_VGPU_CENSUS_DIR" "$PSScriptRoot\runs\census"
        if (-not $DryRun) { New-Item -ItemType Directory -Force $env:REIMS_VGPU_CENSUS_DIR | Out-Null }
    }
    if ($env:VK_DRIVER_FILES) {
        Write-Host "note: VK_DRIVER_FILES=$env:VK_DRIVER_FILES overrides the Vulkan loader's choice (normally the NVIDIA ICD)" -ForegroundColor Yellow
        $envSet["VK_DRIVER_FILES"] = $env:VK_DRIVER_FILES
    }
    # Programs install implicit Vulkan layers that every Vulkan process loads: overlays and
    # capture hooks (seen here: TikTok LIVE Studio's game_detour_64.dll, Epic Online Services'
    # overlay). Each one sits between the runtime and the driver on every call and present, and
    # QEMU's GPU device needs none of them. The loader's `~implicit~` turns them all off.
    if (-not $KeepVkLayers) { Set-EnvDefault "VK_LOADER_LAYERS_DISABLE" "~implicit~" }
}

# The fork's TCG speed-ups (run-vphone.sh documents each).
Set-EnvDefault "VPHONE_TARGET_PAGE_BITS" "14"
Set-EnvDefault "VPHONE_TLB_RANGE_PAGES" "1"
Set-EnvDefault "VPHONE_XPAGE_LINKS" "1"
Set-EnvDefault "VPHONE_RET_PREFETCH" "2"
Set-EnvDefault "QEMU_TLB_UNSYNCED" "2"
Set-EnvDefault "VPHONE_XHCI_NONREMOVABLE" "0x1fe"
$qargs += @("-display", "none")

# Virtualization.framework's VZVirtioSocketDevice is virtio-vsock. vhost-vsock-pci needs Linux's
# vhost-vsock; virtio-vsock-sock-pci runs the transport in QEMU (= VSOCK=uds) and listens on
# 127.0.0.1:$VsockPort: a client sends "CONNECT 1339\n", gets "OK <n>\n", then talks to vphoned
# (tools\vphoned.py). It advertises 4 MiB of credit and asks iOS for credit updates.
$cid = 3
if (Test-Path "$Vm\cid") {
    $c = Read-VmFile "cid"
    if ($c) {
        if ($c -notmatch '^\d+$') { throw "run-vphone.ps1: cannot read CID '$c' in $Vm\cid" }
        $cid = [long]$c
    }
}
$vsock = $null
if ($VsockPort -and $cid) {
    if ($VsockPort -lt 1 -or $VsockPort -gt 65535) { throw "run-vphone.ps1: -VsockPort must be from 1 to 65535 (0 = off)" }
    if ($cid -le 2) { throw "run-vphone.ps1: the guest CID must be greater than 2 (got $cid)" }
    $vsock = "virtio-vsock-sock-pci,guest-cid=$cid,host-port=$VsockPort"
    $qargs += @("-device", $vsock)
}

if ($MonitorPort) { $qargs += @("-monitor", "tcp:127.0.0.1:$MonitorPort,server,nowait") }
if ($Log) { $qargs += @("-D", $Log) }
if ($DFlags) { $qargs += @("-d", $DFlags) }
if ($Trace) { $qargs += @("-trace", $Trace) }
if ($Gdb) { $qargs += @("-gdb", "tcp:127.0.0.1:$Gdb", "-S") }

$all = @("-M", $machine) + $qargs + $ExtraArgs

# A CPU list (0-11, 0,2,4-7) as an affinity mask
$mask = [long]0
$cpuList = $Cpus -join ","
if ($cpuList) {
    foreach ($part in $cpuList -split ',') {
        if ($part -match '^(\d+)-(\d+)$') { $a = [int]$Matches[1]; $b = [int]$Matches[2] }
        elseif ($part -match '^\d+$') { $a = $b = [int]$part }
        else { throw "run-vphone.ps1: cannot read CPU list '$cpuList'" }
        for ($i = $a; $i -le $b; $i++) { $mask = $mask -bor ([long]1 -shl $i) }
    }
}

function Format-Arg([string] $a) { if ($a -match '[\s"]') { '"' + ($a -replace '"', '\"') + '"' } else { $a } }

if ($DryRun) {
    Write-Host "# environment"
    foreach ($k in $envSet.Keys) { Write-Host "`$env:$k = '$($envSet[$k])'" }
    if ($mask) { Write-Host ("# affinity mask 0x{0:x} ({1})" -f $mask, $cpuList) }
    if ($vsock) { Write-Host "# vsock: guest CID $cid, host side tcp 127.0.0.1:$VsockPort (tools\vphoned.py --port $VsockPort)" }
    else { Write-Host "# vsock: off" }
    Write-Host "# command"
    Write-Host ((@(Format-Arg $Qemu) + ($all | ForEach-Object { Format-Arg $_ })) -join " ")
    exit 0
}

New-Item -ItemType Directory -Force $runDir | Out-Null
Write-Host "QEMU: $Qemu"
Write-Host "machine: $machine"
Write-Host "logs: $runDir"
if ($vsock) { Write-Host "vsock: guest CID $cid, tcp 127.0.0.1:$VsockPort" }
((@(Format-Arg $Qemu) + ($all | ForEach-Object { Format-Arg $_ })) -join " ") | Set-Content -Encoding utf8 "$runDir\cmdline.txt"

$self = [Diagnostics.Process]::GetCurrentProcess()
$oldMask = $self.ProcessorAffinity
if ($mask) { $self.ProcessorAffinity = [IntPtr]$mask }   # QEMU inherits it
# No 2>&1 / Tee here: in Windows PowerShell 5.1, redirecting a native program's stderr under
# ErrorActionPreference=Stop turns its first stderr line into a terminating error, and piping
# stdout would take the console away from the serial console.
$ErrorActionPreference = "Continue"
try {
    & $Qemu @all
    $rc = $LASTEXITCODE
} finally {
    if ($mask) { $self.ProcessorAffinity = $oldMask }
}
exit $rc
