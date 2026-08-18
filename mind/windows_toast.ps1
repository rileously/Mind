# Puts a call on screen, with buttons that lead somewhere.
#
# Run by mind\windows_toast.py rather than by hand. Kept as a script for the
# same reason the OCR and printing ones are: this is Windows API work that
# reads far better in the language those APIs were written for, and a script
# can be read by whoever wonders what Mind is showing them.
#
# The buttons activate a protocol rather than a COM server. An unpackaged
# program can register "mind:" in its own registry hive with no elevation and
# no component to host; Windows then launches Mind with the URI, and Mind hands
# it to the copy already running. A COM activator is the documented way and
# needs a class registered, a factory hosted and a GUID stamped on a shortcut,
# for the same outcome.
param(
    [Parameter(Mandatory = $true)][string]$Aumid,
    [string]$Title = "Incoming call",
    [string]$Body = "",
    [string]$Attribution = "",
    [string]$AnswerUri = "",
    [string]$RejectUri = "",
    [string]$MuteUri = "",
    [string]$MuteLabel = "Mute",
    [string]$RejectLabel = "Reject",
    [string]$Tag = "mind-call",
    [string]$Group = "mind",
    [switch]$Ringing,
    [switch]$Dismiss,
    # Stays on screen and counts, until this process is ended.
    [switch]$Live,
    [int]$MaxMinutes = 240
)

$ErrorActionPreference = "Stop"
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]

if ($Dismiss) {
    # A call notification that outlives the call is worse than none at all.
    try {
        [Windows.UI.Notifications.ToastNotificationManager]::History.Remove($Tag, $Group, $Aumid)
        "dismissed"
    } catch {
        "nothing to dismiss"
    }
    exit 0
}

function Escape([string]$value) {
    return [System.Security.SecurityElement]::Escape($value)
}

function CallData([string]$duration, [int]$sequence) {
    # Built from a dictionary: PowerShell sees the map on NotificationData as a
    # bare COM object with no methods it can reach.
    $pairs = New-Object 'System.Collections.Generic.Dictionary[string,string]'
    $pairs.Add("duration", $duration)
    $pairs.Add("progressValue", "indeterminate")
    $data = New-Object Windows.UI.Notifications.NotificationData($pairs)
    $data.SequenceNumber = $sequence
    return $data
}

function Spoken([timespan]$span) {
    if ($span.TotalHours -ge 1) {
        return "{0}:{1:d2}:{2:d2}" -f [int]$span.TotalHours, $span.Minutes, $span.Seconds
    }
    return "{0:d2}:{1:d2}" -f $span.Minutes, $span.Seconds
}

if ($Live) {
    # The duration is carried by a progress element rather than by text.
    # Windows substitutes {placeholders} there and does not substitute them in
    # text - it accepts the update either way and answers Succeeded, which is
    # how the braces ended up on screen. And it is updated in place rather than
    # shown again: shown again, the notification hides and reappears once a
    # second, which is what it did.
    [void][Windows.UI.Notifications.NotificationData, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $liveActions = '<action content="' + (Escape $MuteLabel) + '" activationType="protocol" arguments="' + (Escape $MuteUri) + '" />'
    $liveActions += '<action content="Hang up" activationType="protocol" arguments="' + (Escape $RejectUri) + '" />'
    $whoLine = if ($Body) { '<text>' + (Escape $Body) + '</text>' } else { "" }
    $liveXml = @"
<toast scenario="incomingCall" launch="mind://call/show" activationType="protocol">
  <visual>
    <binding template="ToastGeneric">
      <text>$(Escape $Title)</text>
      $whoLine
      <progress value="{progressValue}" status="{duration}" />
      <text placement="attribution">$(Escape $Attribution)</text>
    </binding>
  </visual>
  <actions>$liveActions</actions>
  <audio silent="true" />
</toast>
"@
    $liveDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $liveDoc.LoadXml($liveXml)
    $liveToast = New-Object Windows.UI.Notifications.ToastNotification $liveDoc
    $liveToast.Tag = $Tag
    $liveToast.Group = $Group
    $liveToast.Data = CallData "00:00" 1
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($Aumid)
    $notifier.Show($liveToast)
    "shown"

    $started = Get-Date
    $sequence = 2
    while (((Get-Date) - $started).TotalMinutes -lt $MaxMinutes) {
        Start-Sleep -Seconds 1
        $elapsed = (Get-Date) - $started
        $result = $notifier.Update((CallData (Spoken $elapsed) $sequence), $Tag, $Group)
        $sequence++
        # Gone from the screen and from the action centre: whatever it was
        # counting has been dealt with somewhere else.
        if ("$result" -eq "NotificationNotFound") { break }
    }
    exit 0
}

$actions = ""
if ($AnswerUri) {
    $actions += '<action content="Answer" activationType="protocol" arguments="' + (Escape $AnswerUri) + '" />'
}
if ($MuteUri) {
    $actions += '<action content="' + (Escape $MuteLabel) + '" activationType="protocol" arguments="' + (Escape $MuteUri) + '" />'
}
if ($RejectUri) {
    # "Reject" while it is ringing, "Hang up" once somebody is talking: the
    # same key, and two different things to be about to do.
    $actions += '<action content="' + (Escape $RejectLabel) + '" activationType="protocol" arguments="' + (Escape $RejectUri) + '" />'
}
if ($actions) { $actions = "<actions>$actions</actions>" }

# incomingCall keeps it on screen instead of fading after a few seconds, which
# is the whole point: a call notification nobody is looking at yet.
$scenario = if ($Ringing) { ' scenario="incomingCall"' } else { "" }
$audio = if ($Ringing) {
    '<audio src="ms-winsoundevent:Notification.Looping.Call" loop="true" />'
} else {
    '<audio silent="true" />'
}
$attributionLine = if ($Attribution) {
    '<text placement="attribution">' + (Escape $Attribution) + '</text>'
} else { "" }

$xml = @"
<toast$scenario launch="$(Escape $AnswerUri)" activationType="protocol">
  <visual>
    <binding template="ToastGeneric">
      <text>$(Escape $Title)</text>
      <text>$(Escape $Body)</text>
      $attributionLine
    </binding>
  </visual>
  $actions
  $audio
</toast>
"@

$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
$toast.Tag = $Tag
$toast.Group = $Group
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($Aumid).Show($toast)
"shown"
