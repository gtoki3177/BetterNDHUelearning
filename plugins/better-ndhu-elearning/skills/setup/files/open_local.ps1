# BetterElearning: betterel: link handler.
# Opens a course file under the course root folder (the parent of _moodle) with its default app.
# Safety: only paths inside that root folder; only document types are opened directly.
# .zip and folders open in File Explorer; anything else (exe, bat, ...) is only shown in Explorer, never run.
param([string]$Url)
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$log  = Join-Path $PSScriptRoot 'open_local_log.txt'
$cmp  = [StringComparison]::OrdinalIgnoreCase
$docs = @('.pdf','.ppt','.pptx','.doc','.docx','.xls','.xlsx','.txt','.md','.csv')
try {
    $q = $Url -replace '^betterel:(//)?', ''
    if ($q -match '(?:^|[?&])p=([^&]*)') { $enc = $Matches[1] } else { throw "no p= in $Url" }
    $full = [IO.Path]::GetFullPath([uri]::UnescapeDataString($enc))
    if (-not $full.StartsWith($root + '\', $cmp)) { throw "outside root: $full" }
    if (Test-Path -LiteralPath $full -PathType Container) {
        Start-Process explorer.exe -ArgumentList "`"$full`""
    } elseif (Test-Path -LiteralPath $full -PathType Leaf) {
        $ext = [IO.Path]::GetExtension($full).ToLowerInvariant()
        if ($docs -contains $ext) { Invoke-Item -LiteralPath $full }
        else { Start-Process explorer.exe -ArgumentList "/select,`"$full`"" }
    } else {
        $d = Split-Path $full -Parent
        while ($d -and -not (Test-Path -LiteralPath $d)) { $d = Split-Path $d -Parent }
        if ($d -and ($d + '\').StartsWith($root + '\', $cmp)) { Start-Process explorer.exe -ArgumentList "`"$d`"" }
        throw "missing: $full"
    }
} catch {
    Add-Content -LiteralPath $log -Value ("{0}  {1}" -f (Get-Date -Format s), $_) -Encoding UTF8
}
