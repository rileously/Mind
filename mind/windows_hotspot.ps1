# Turns this PC's Wi-Fi into an access point, and says what it is doing.
#
# Run by mind\hotspot.py rather than by hand. Kept as a script for the same
# reason the notification and printing ones are: this is WinRT work, and the
# projection Windows PowerShell carries reads better than the reflection the
# same calls need from Python.
#
# "netsh wlan set hostednetwork" is the older way to do this and most current
# drivers answer "Hosted network supported: No", because the feature moved to
# the Mobile Hotspot in Settings. NetworkOperatorTetheringManager is what that
# page itself drives, so this is the same switch, pressed from here.
param(
    [ValidateSet("status", "start", "stop", "configure")]
    [string]$Action = "status",
    [string]$Ssid = "",
    [string]$Passphrase = "",
    # Auto lets Windows choose, which on a PC already on 2.4 GHz usually means
    # 2.4 GHz anyway - but "usually" is not something to build a room's signal
    # on, and the lower band is the one that goes through walls.
    [ValidateSet("", "auto", "2.4", "5", "6")]
    [string]$Band = ""
)

$ErrorActionPreference = "Stop"

# Every answer is key=value lines. The caller parses it; nothing here is meant
# to be read as prose, and the passphrase is never one of the keys.
function Say([string]$key, $value) {
    Write-Output "$key=$value"
}

function Fail([string]$detail) {
    Say "ok" "0"
    Say "detail" $detail
    exit 0
}

# WinRT hands back promises. These two turn one into a value, for the operations
# that return a result and for the ones that only finish.
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskOperation = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq "AsTask" -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
    })[0]
$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq "AsTask" -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.FullName -eq "Windows.Foundation.IAsyncAction"
    })[0]

function AwaitOperation($operation, $resultType) {
    $asTask = $asTaskOperation.MakeGenericMethod($resultType)
    $task = $asTask.Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    return $task.Result
}

function AwaitAction($operation) {
    $task = $asTaskAction.Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
}

try {
    $profile = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]::GetInternetConnectionProfile()
} catch {
    Fail "Windows would not say what this PC is connected through."
}
if ($null -eq $profile) {
    Fail "This PC has no internet connection to share."
}

try {
    $manager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]::CreateFromConnectionProfile($profile)
} catch {
    Fail "This adapter cannot host a hotspot. Its driver may not support one."
}
if ($null -eq $manager) {
    Fail "This adapter cannot host a hotspot."
}

# Reported before and after any change, so the caller never has to guess.
function Report() {
    Say "ok" "1"
    # The enum: Unknown, On, Off, InTransition. Lowercased, because the caller
    # compares it and the panel prints its own words anyway.
    Say "state" ("$($manager.TetheringOperationalState)".ToLowerInvariant())
    try {
        Say "clients" $manager.ClientCount
    } catch {
        Say "clients" "0"
    }
    # One snapshot for both, so the name and the band cannot disagree.
    $current = $null
    try { $current = $manager.GetCurrentAccessPointConfiguration() } catch { }
    if ($null -ne $current) {
        Say "ssid" $current.Ssid
        Say "band" $current.Band
    } else {
        Say "ssid" ""
        Say "band" "Auto"
    }
}

switch ($Action) {
    "status" {
        Report
    }
    "configure" {
        if ([string]::IsNullOrWhiteSpace($Ssid)) {
            Fail "A hotspot needs a name."
        }
        if ($Passphrase.Length -lt 8) {
            Fail "A hotspot password must be at least 8 characters."
        }
        try {
            $access = $manager.GetCurrentAccessPointConfiguration()
            $access.Ssid = $Ssid
            $access.Passphrase = $Passphrase
            if ($Band) {
                # Not fatal on its own: an adapter that will not sit on the
                # asked-for band should still come up on the one it prefers,
                # carrying the name and password that were the point of the call.
                $wanted = switch ($Band) {
                    "2.4" { "TwoPointFourGigahertz" }
                    "5"   { "FiveGigahertz" }
                    "6"   { "SixGigahertz" }
                    default { "Auto" }
                }
                try { $access.Band = $wanted } catch { Say "bandrefused" $wanted }
            }
            AwaitAction $manager.ConfigureAccessPointAsync($access)
        } catch {
            Fail "Windows refused the hotspot name or password."
        }
        Report
    }
    "start" {
        if ("$($manager.TetheringOperationalState)" -eq "On") {
            Report
            break
        }
        try {
            $result = AwaitOperation $manager.StartTetheringAsync() ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
        } catch {
            Fail "Windows could not start the hotspot."
        }
        if ("$($result.Status)" -ne "Success") {
            $detail = $result.AdditionalErrorMessage
            if ([string]::IsNullOrWhiteSpace($detail)) {
                $detail = "Windows would not start the hotspot ($($result.Status))."
            }
            Fail $detail
        }
        Report
    }
    "stop" {
        if ("$($manager.TetheringOperationalState)" -eq "Off") {
            Report
            break
        }
        try {
            $result = AwaitOperation $manager.StopTetheringAsync() ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
        } catch {
            Fail "Windows could not stop the hotspot."
        }
        if ("$($result.Status)" -ne "Success") {
            $detail = $result.AdditionalErrorMessage
            if ([string]::IsNullOrWhiteSpace($detail)) {
                $detail = "Windows would not stop the hotspot ($($result.Status))."
            }
            Fail $detail
        }
        Report
    }
}
