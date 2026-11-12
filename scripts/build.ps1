[CmdletBinding()]
param(
    [string]$Mihomo = '',
    [string]$Tag = 'v1.19.34',
    [switch]$FetchCore
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root  = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root '.tools'
$Dist  = Join-Path $Root 'dist'
$fail  = $false

if ($FetchCore) {
    New-Item -ItemType Directory -Force -Path $Tools | Out-Null
    $zip = Join-Path $Tools 'mihomo.zip'
    $url = "https://github.com/MetaCubeX/mihomo/releases/download/$Tag/mihomo-windows-amd64-$Tag.zip"
    Write-Host "下载 $url"
    Invoke-WebRequest -Uri $url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $Tools -Force
    Get-ChildItem $Tools -Filter 'mihomo*.exe' |
        Where-Object { $_.Name -ne 'mihomo.exe' } |
        ForEach-Object { Move-Item -Force $_.FullName (Join-Path $Tools 'mihomo.exe') }
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
}

$core = $Mihomo
if (-not $core) {
    $local = Join-Path $Tools 'mihomo.exe'
    if (Test-Path $local) { $core = $local }
    else {
        $cmd = Get-Command mihomo.exe -ErrorAction SilentlyContinue
        if ($cmd) { $core = $cmd.Source }
    }
}
if (-not $core -or -not (Test-Path $core)) {
    throw ' 没找到 mihomo 内核：加 -FetchCore 自动下载，或用 -Mihomo <路径> 指定（例如 v2rayN 自带的那个）。'
}

Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue

foreach ($behavior in @('domain', 'ipcidr')) {
    $src = Join-Path $Root "rules\$behavior"
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $Dist $behavior
    New-Item -ItemType Directory -Force -Path $dst | Out-Null

    Get-ChildItem $src -Filter *.txt | ForEach-Object {
        $name = $_.BaseName
        $out  = Join-Path $dst "$name.mrs"
        $log  = & $core convert-ruleset $behavior text $_.FullName $out 2>&1
        if ("$log" -match 'invalid') {
            Write-Host "!! $($_.Name) 含非法行：" -ForegroundColor Red
            $log | ForEach-Object { Write-Host "   $_" }
            $script:fail = $true
        }
        Copy-Item $_.FullName (Join-Path $dst "$name.txt") -Force
        '{0,-28} {1,8} B (mrs)' -f "$behavior/$name", (Get-Item $out).Length
    }
}

if ($fail) { Write-Host '构建失败：先修掉上面标出的行' -ForegroundColor Red; exit 1 }
Write-Host "产物目录：$Dist"
