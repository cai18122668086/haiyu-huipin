param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$manifestPath = [System.IO.Path]::GetFullPath($Manifest)
$outputPath = [System.IO.Path]::GetFullPath($OutputDir)
if (-not [System.IO.File]::Exists($manifestPath)) {
    throw "Segmentation manifest not found: $manifestPath"
}
[System.IO.Directory]::CreateDirectory($outputPath) | Out-Null

$data = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
if ($null -eq $data.references -or $data.references.Count -lt 1) {
    throw 'Manifest must contain at least one reference.'
}

$resolved = @()
$seenIds = @{}
foreach ($reference in $data.references) {
    $sourcePath = [System.IO.Path]::GetFullPath([string]$reference.source_image)
    if (-not [System.IO.File]::Exists($sourcePath)) {
        throw "Reference source not found: $sourcePath"
    }
    if ($null -eq $reference.units -or $reference.units.Count -lt 1) {
        throw "Reference has no units: $sourcePath"
    }

    if ([string]$reference.segmentation_basis -ne 'SCREEN') {
        throw "Reference segmentation_basis must be SCREEN: $sourcePath"
    }
    $declaredCounts = @{}
    foreach ($row in @($reference.column_screen_counts)) {
        $column = [int]$row.column_index
        $count = [int]$row.screen_count
        if ($column -lt 0 -or $count -lt 1) { throw "Invalid column_screen_counts entry for $sourcePath" }
        if ($declaredCounts.ContainsKey($column)) { throw "Duplicate column_screen_counts entry for column $column" }
        if ([string]::IsNullOrWhiteSpace([string]$row.count_evidence)) { throw "Column $column needs count_evidence" }
        $declaredCounts[$column] = $count
    }
    if ($declaredCounts.Count -lt 1) { throw "Reference needs column_screen_counts: $sourcePath" }

    $unitsByColumn = @{}
    foreach ($unit in @($reference.units)) {
        $column = [int]$unit.column_index
        $screen = [int]$unit.screen_index
        if (-not $declaredCounts.ContainsKey($column)) { throw "Unit $($unit.id) uses undeclared column $column" }
        if ($screen -lt 0) { throw "Unit $($unit.id) needs a non-negative screen_index" }
        if ([int]$unit.screen_count_in_column -ne [int]$declaredCounts[$column]) { throw "Unit $($unit.id) screen_count_in_column disagrees with column $column" }
        if ([string]::IsNullOrWhiteSpace([string]$unit.screen_boundary_reason)) { throw "Unit $($unit.id) needs screen_boundary_reason" }
        if (-not $unitsByColumn.ContainsKey($column)) { $unitsByColumn[$column] = @() }
        $unitsByColumn[$column] += $screen
    }
    foreach ($column in $declaredCounts.Keys) {
        $actual = @($unitsByColumn[$column] | Sort-Object)
        $expected = @(0..([int]$declaredCounts[$column] - 1))
        if ($actual.Count -ne $expected.Count -or (Compare-Object $actual $expected)) {
            throw "Column $column must contain every screen_index from 0 through $([int]$declaredCounts[$column] - 1) exactly once"
        }
    }

    $source = [System.Drawing.Image]::FromFile($sourcePath)
    try {
        foreach ($unit in $reference.units) {
            $id = [string]$unit.id
            if ([string]::IsNullOrWhiteSpace($id)) { throw 'Every unit requires an id.' }
            if ($seenIds.ContainsKey($id)) { throw "Duplicate unit id: $id" }
            $seenIds[$id] = $true

            $x = [double]$unit.rect.x
            $y = [double]$unit.rect.y
            $w = [double]$unit.rect.w
            $h = [double]$unit.rect.h
            if ($x -lt 0 -or $y -lt 0 -or $w -le 0 -or $h -le 0 -or ($x + $w) -gt 1.000001 -or ($y + $h) -gt 1.000001) {
                throw "Unit $id has an invalid normalized rectangle."
            }

            $left = [Math]::Max(0, [Math]::Floor($x * $source.Width))
            $top = [Math]::Max(0, [Math]::Floor($y * $source.Height))
            $right = [Math]::Min($source.Width, [Math]::Ceiling(($x + $w) * $source.Width))
            $bottom = [Math]::Min($source.Height, [Math]::Ceiling(($y + $h) * $source.Height))
            $cropWidth = [int]($right - $left)
            $cropHeight = [int]($bottom - $top)
            if ($cropWidth -lt 32 -or $cropHeight -lt 32) {
                throw "Unit $id is too small after cropping: ${cropWidth}x${cropHeight}."
            }

            $safeId = [System.Text.RegularExpressions.Regex]::Replace($id, '[^A-Za-z0-9._-]', '_')
            $cropPath = [System.IO.Path]::Combine($outputPath, "$safeId.png")
            $bitmap = New-Object System.Drawing.Bitmap($cropWidth, $cropHeight, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
            try {
                $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
                try {
                    $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
                    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                    $dest = New-Object System.Drawing.Rectangle(0, 0, $cropWidth, $cropHeight)
                    $src = New-Object System.Drawing.Rectangle([int]$left, [int]$top, $cropWidth, $cropHeight)
                    $graphics.DrawImage($source, $dest, $src, [System.Drawing.GraphicsUnit]::Pixel)
                }
                finally { $graphics.Dispose() }
                $bitmap.Save($cropPath, [System.Drawing.Imaging.ImageFormat]::Png)
            }
            finally { $bitmap.Dispose() }

            $cropSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $cropPath).Hash.ToLowerInvariant()
            $resolved += [ordered]@{
                id = $id
                source_image = $sourcePath
                classification = [string]$reference.classification
                source_rect = [ordered]@{ x = $x; y = $y; w = $w; h = $h }
                pixel_rect = [ordered]@{ x = [int]$left; y = [int]$top; w = $cropWidth; h = $cropHeight }
                segmentation_basis = 'SCREEN'
                column_index = [int]$unit.column_index
                screen_index = [int]$unit.screen_index
                screen_count_in_column = [int]$unit.screen_count_in_column
                screen_boundary_reason = [string]$unit.screen_boundary_reason
                preliminary_duty = [string]$unit.preliminary_duty
                confidence = if ($null -eq $unit.confidence) { $null } else { [double]$unit.confidence }
                crop_path = $cropPath
                crop_sha256 = $cropSha256
                pixel_width = $cropWidth
                pixel_height = $cropHeight
                ai_reference_observation = $unit.ai_reference_observation
            }
        }
    }
    finally { $source.Dispose() }
}

$resolvedManifest = [ordered]@{
    source_manifest = $manifestPath
    generated_at = [DateTime]::UtcNow.ToString('o')
    units = $resolved
}
$resolvedPath = [System.IO.Path]::Combine($outputPath, 'reference-units.resolved.json')
$resolvedManifest | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 -LiteralPath $resolvedPath
$resolvedManifest | ConvertTo-Json -Depth 12
