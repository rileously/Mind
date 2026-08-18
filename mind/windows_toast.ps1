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

function LiveToast([string]$caller, [string]$duration, [string]$title, [string]$attribution, [string]$muteLabel, [string]$muteUri, [string]$rejectUri, [string]$tag, [string]$group) {
    # Written into the XML rather than bound with {placeholders}. Windows
    # accepts a bound update and reports Succeeded, and leaves the braces on
    # screen: the substitution never happens for text. Showing it again under
    # the same tag replaces the one already there.
    $acts = '<action content="' + (Escape $muteLabel) + '" activationType="protocol" arguments="' + (Escape $muteUri) + '" />'
    $acts += '<action content="Hang up" activationType="protocol" arguments="' + (Escape $rejectUri) + '" />'
    $body = if ($duration) { "$caller - $duration" } else { $caller }
    $liveXml = @"
<toast scenario="incomingCall" launch="mind://call/show" activationType="protocol">
  <visual>
    <binding template="ToastGeneric">
      <text>$(Escape $title)</text>
      <text>$(Escape $body)</text>
      <text placement="attribution">$(Escape $attribution)</text>
    </binding>
  </visual>
  <actions>$acts</actions>
  <audio silent="true" />
</toast>
"@
    $liveDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $liveDoc.LoadXml($liveXml)
    $made = New-Object Windows.UI.Notifications.ToastNotification $liveDoc
    $made.Tag = $tag
    $made.Group = $group
    # Nothing new has happened; it is the same call one second later.
    $made.SuppressPopup = $false
    return $made
}

function Spoken([timespan]$span) {
    if ($span.TotalHours -ge 1) {
        return "{0}:{1:d2}:{2:d2}" -f [int]$span.TotalHours, $span.Minutes, $span.Seconds
    }
    return "{0:d2}:{1:d2}" -f $span.Minutes, $span.Seconds
}

if ($Live) {
    # A call on screen for as long as it lasts, with the time it has taken so
    # far. The text is updated in place rather than the notification being
    # shown again: shown again, it would re-announce itself once a second.
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($Aumid)
    $notifier.Show((LiveToast $Body "00:00" $Title $Attribution $MuteLabel $MuteUri $RejectUri $Tag $Group))
    "shown"

    $started = Get-Date
    while (((Get-Date) - $started).TotalMinutes -lt $MaxMinutes) {
        Start-Sleep -Seconds 1
        $elapsed = (Get-Date) - $started
        $notifier.Show((LiveToast $Body (Spoken $elapsed) $Title $Attribution $MuteLabel $MuteUri $RejectUri $Tag $Group))
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
