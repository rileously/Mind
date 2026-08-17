# Printing for files that arrive over Telegram.
#
# Windows has no single way to print a file, so there are three, chosen by the
# caller from the file's type:
#
#   Image  A PrintDocument that draws the picture scaled to the page.
#   Text   A PrintDocument that lays the text out and paginates it.
#   Verb   The registered "printto" handler - Acrobat for PDF, Word, Excel. The
#          only way to print those formats without reimplementing them.
#
# Paper size and colour are set on the job itself for Image and Text, which needs
# no special rights. The Verb path cannot do that: the handler is another
# application and takes nothing but a printer name, so those settings have to be
# written to the printer's own configuration - and Windows refuses that to anyone
# without administrator rights. When it is refused the file is still printed, on
# whatever paper the printer is set to, and a warning says so. Printing the file
# and explaining one limitation beats refusing to print at all.
#
# Warnings are written to stdout as "warning: ..." lines. The caller shows them
# to the user; the last line is "printed" when the job was sent.

[CmdletBinding()]
param(
    [switch]$List,
    [string]$Printer = "",
    [string]$File = "",
    [string]$Paper = "",
    [ValidateSet("verb", "text", "image")][string]$Strategy = "verb",
    # A word rather than a boolean. Arguments arrive as text when a script is run
    # with -File, and PowerShell will not bind the text "$true" to a [bool]
    # parameter - it fails before the script runs, which is how all printing was
    # broken while the arguments still looked right.
    [ValidateSet("colour", "mono")][string]$Ink = "colour",
    [ValidateSet("one", "both")][string]$Sides = "one",
    [ValidateRange(1, 10)][int]$Copies = 1,
    [int]$SpoolTimeoutSeconds = 25,
    # A physical safety limit. A 25 MB text file is thousands of pages, and the
    # person who tapped Print may be nowhere near the printer.
    [int]$MaxTextPages = 20
)

$ErrorActionPreference = "Stop"
$Color = ($Ink -eq "colour")

# Loaded here, before anything reaches for a type inside it. The image branch
# opens the picture before it builds the document, so leaving this to the
# document helper meant every image print failed with "Unable to find type".
# The listing needs it too, to ask each printer whether it can print both sides.
if ($List -or $Strategy -ne "verb") {
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
}

if ($List) {
    # Win32_Printer knows which one is the default; Get-Printer does not.
    $default = ""
    try {
        $default = (Get-CimInstance Win32_Printer -ErrorAction Stop |
            Where-Object { $_.Default } | Select-Object -First 1 -ExpandProperty Name)
    } catch { $default = "" }
    $printers = @(Get-Printer -ErrorAction Stop | ForEach-Object {
        # Asked of the driver rather than assumed, so "Both sides" is only ever
        # offered where it does something.
        $duplex = $false
        try {
            $probe = New-Object System.Drawing.Printing.PrintDocument
            $probe.PrinterSettings.PrinterName = $_.Name
            $duplex = [bool]$probe.PrinterSettings.CanDuplex
        } catch {
            $duplex = $false
        }
        [pscustomobject]@{
            name    = $_.Name
            default = ($_.Name -eq $default)
            duplex  = $duplex
        }
    })
    # An array wrapper so a single printer does not serialise as a bare object.
    ConvertTo-Json -InputObject @{ printers = $printers } -Depth 3 -Compress
    exit 0
}

if (-not $Printer) { throw "A printer name is required." }
if (-not $File) { throw "A file is required." }
$path = [IO.Path]::GetFullPath($File)
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "That file is no longer there."
}

function New-Document([string]$PrinterName, [string]$Name) {
    $document = New-Object System.Drawing.Printing.PrintDocument
    $document.PrinterSettings.PrinterName = $PrinterName
    if (-not $document.PrinterSettings.IsValid) {
        throw "Windows does not recognise the printer '$PrinterName'."
    }
    $document.DocumentName = $Name
    $document.DefaultPageSettings.Color = $Color
    if ($Sides -eq "both") {
        if ($document.PrinterSettings.CanDuplex) {
            # Vertical is the long-edge flip, which is how a document is read.
            $document.PrinterSettings.Duplex = [System.Drawing.Printing.Duplex]::Vertical
        } else {
            Write-Output "warning: this printer prints one side only, so both sides was ignored."
        }
    }
    # Native copies when the driver will take them, because it spools once and
    # can collate. Where it reports a maximum of one, the caller prints again
    # instead - which is the only thing left that produces two pieces of paper.
    $script:copiesToRepeat = 1
    if ($Copies -gt 1) {
        if ($document.PrinterSettings.MaximumCopies -ge $Copies) {
            $document.PrinterSettings.Copies = $Copies
        } else {
            $script:copiesToRepeat = $Copies
        }
    }
    if ($Paper) {
        $size = $document.PrinterSettings.PaperSizes |
            Where-Object { $_.Kind -eq $Paper -or $_.PaperName -eq $Paper } |
            Select-Object -First 1
        if ($size) {
            $document.DefaultPageSettings.PaperSize = $size
        } else {
            Write-Output "warning: this printer has no $Paper paper, so its own size was used."
        }
    }
    return $document
}

switch ($Strategy) {
    "image" {
        $image = [System.Drawing.Image]::FromFile($path)
        try {
            $document = New-Document $Printer ([IO.Path]::GetFileName($path))
            $document.add_PrintPage({
                param($sender, $event)
                $bounds = $event.MarginBounds
                # Fit inside the margins without distorting the picture. Nothing
                # is rotated: a landscape photo on portrait paper simply comes
                # out smaller, which is what a print preview would show.
                $scale = [Math]::Min(
                    $bounds.Width / $image.Width, $bounds.Height / $image.Height)
                $width = [int][Math]::Floor($image.Width * $scale)
                $height = [int][Math]::Floor($image.Height * $scale)
                $left = $bounds.Left + [int](($bounds.Width - $width) / 2)
                $top = $bounds.Top + [int](($bounds.Height - $height) / 2)
                $event.Graphics.DrawImage($image, $left, $top, $width, $height)
                $event.HasMorePages = $false
            })
            for ($copy = 0; $copy -lt $script:copiesToRepeat; $copy++) {
                $document.Print()
            }
        } finally {
            $image.Dispose()
        }
    }

    "text" {
        $content = [IO.File]::ReadAllText($path)
        if (-not $content.Trim()) { throw "That file has nothing in it to print." }
        $script:offset = 0
        $script:pages = 0
        $script:truncated = $false
        $font = New-Object System.Drawing.Font "Consolas", 10
        try {
            $document = New-Document $Printer ([IO.Path]::GetFileName($path))
            $document.add_PrintPage({
                param($sender, $event)
                $bounds = $event.MarginBounds
                $remaining = $content.Substring($script:offset)
                $format = New-Object System.Drawing.StringFormat
                $area = New-Object System.Drawing.SizeF($bounds.Width, $bounds.Height)
                $characters = 0
                $lines = 0
                # MeasureString reports how much of the text fits, which is what
                # makes the next page start exactly where this one stopped.
                [void]$event.Graphics.MeasureString(
                    $remaining, $font, $area, $format, [ref]$characters, [ref]$lines)
                # RectangleF explicitly: the overload that wraps text into a box
                # takes one, and MarginBounds is a Rectangle, which binds to the
                # single-point overload instead and fails.
                $box = New-Object System.Drawing.RectangleF(
                    $bounds.Left, $bounds.Top, $bounds.Width, $bounds.Height)
                $event.Graphics.DrawString(
                    $remaining, $font, [System.Drawing.Brushes]::Black, $box, $format)
                # A page that fits nothing would loop forever.
                if ($characters -le 0) { $event.HasMorePages = $false; return }
                $script:offset += $characters
                $script:pages += 1
                if ($script:pages -ge $MaxTextPages -and $script:offset -lt $content.Length) {
                    $script:truncated = $true
                    $event.HasMorePages = $false
                    return
                }
                $event.HasMorePages = ($script:offset -lt $content.Length)
            })
            for ($copy = 0; $copy -lt $script:copiesToRepeat; $copy++) {
                # Each copy starts at the beginning of the text again.
                $script:offset = 0
                $script:pages = 0
                $document.Print()
            }
            if ($script:truncated) {
                Write-Output "warning: only the first $MaxTextPages pages were printed."
            }
        } finally {
            $font.Dispose()
        }
    }

    default {
        # The handler takes a printer and nothing else, so paper and colour have
        # to be written to the printer itself, which usually needs rights this
        # process does not have.
        $previous = $null
        try {
            $previous = Get-PrintConfiguration -PrinterName $Printer -ErrorAction Stop
        } catch {
            $previous = $null
        }
        $applied = $false
        if ($previous) {
            $settings = @{ PrinterName = $Printer; Color = $Color }
            if ($Paper) { $settings["PaperSize"] = $Paper }
            if ($Sides -eq "both") { $settings["DuplexingMode"] = "TwoSidedLongEdge" }
            try {
                Set-PrintConfiguration @settings -ErrorAction Stop
                $applied = $true
            } catch {
                # Every one of these goes through the same call, so they are all
                # lost together. Naming only the paper size would leave someone
                # wondering why a colour page came out grey on one side.
                Write-Output ("warning: the paper size, colour and sides need " +
                    "administrator rights for this file type, so the printer's own " +
                    "settings were used.")
            }
        }
        try {
            # ArgumentList becomes the verb's parameters, which is where the
            # printto handler looks for the printer name. There is nowhere to put
            # a number of copies, so the file is simply printed again.
            for ($copy = 0; $copy -lt $Copies; $copy++) {
                Start-Process -FilePath $path -Verb PrintTo -ArgumentList "`"$Printer`"" `
                    -WindowStyle Hidden -ErrorAction Stop
                if ($copy -lt $Copies - 1) { Start-Sleep -Milliseconds 1200 }
            }
            if ($applied) {
                # The handler is another process and prints asynchronously, so the
                # settings must stay in place until the job reaches the queue.
                $deadline = [DateTime]::UtcNow.AddSeconds($SpoolTimeoutSeconds)
                while ([DateTime]::UtcNow -lt $deadline) {
                    if (@(Get-PrintJob -PrinterName $Printer -ErrorAction SilentlyContinue).Count -gt 0) {
                        break
                    }
                    Start-Sleep -Milliseconds 250
                }
            }
        } catch {
            throw "Windows could not print that file: $($_.Exception.Message)"
        } finally {
            if ($applied) {
                try {
                    # Duplex is restored too, or a one-sided printer setting would
                    # be left flipped for everything else on this PC.
                    Set-PrintConfiguration -PrinterName $Printer `
                        -PaperSize $previous.PaperSize -Color $previous.Color `
                        -DuplexingMode $previous.DuplexingMode -ErrorAction Stop
                } catch {
                    Write-Output "warning: the printer's own settings could not be put back."
                }
            }
        }
    }
}

Write-Output "printed"
