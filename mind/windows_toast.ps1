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
    [string]$Tag = "mind-call",
    [string]$Group = "mind",
    [switch]$Ringing,
    [switch]$Dismiss
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

$actions = ""
if ($AnswerUri) {
    $actions += '<action content="Answer" activationType="protocol" arguments="' + (Escape $AnswerUri) + '" />'
}
if ($RejectUri) {
    $actions += '<action content="Reject" activationType="protocol" arguments="' + (Escape $RejectUri) + '" />'
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
