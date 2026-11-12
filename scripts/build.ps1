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
$Tmp = Join-Path $Root '.tmp'
Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

$utf8 = New-Object System.Text.UTF8Encoding($false)

function Invoke-Compile {
    param([string]$Behavior, [string]$Name, [string]$InputFile, [string]$Keep)
    $dst = Join-Path $Dist $Behavior
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $out = Join-Path $dst "$Name.mrs"
    $log = & $core convert-ruleset $Behavior text $InputFile $out 2>&1
    if ("$log" -match 'invalid' -or "$log" -match 'panic') {
        Write-Host "!! $Behavior/$Name 构建异常：" -ForegroundColor Red
        $log | ForEach-Object { Write-Host "   $_" }
        $script:fail = $true
    }
    if ($Keep) { Copy-Item $InputFile (Join-Path $dst "$Name.$Keep") -Force }
    '{0,-28} {1,8} N B (mrs)' -f "$Behavior/$Name", (Get-Item $out).Length
}

# build/ 是 fetch-sources.py 生成的，rules/ 是手写补丁；同名就合并（build 在前，rules 追加）
foreach ($behavior in @('domain', 'ipcidr')) {
    $dirs = @((Join-Path $Root "build\$behavior"), (Join-Path $Root "rules\$behavior"))
    $names = [System.Collections.Generic.SortedSet[string]]::new()
    foreach ($d in $dirs) {
        if (-not (Test-Path $d)) { continue }
        Get-ChildItem $d -Filter *.txt | ForEach-Object { [void]$names.Add($_.BaseName) }
    }
    foreach ($name in $names) {
        $parts = @()
        foreach ($d in $dirs) {
            $f = Join-Path $d "$name.txt"
            if (Test-Path $f) { $parts += (Get-Content $f -Raw) }
        }
        if (-not $parts) { continue }
        $merged = Join-Path $Tmp "$name.txt"
        [IO.File]::WriteAllText($merged, ($parts -join "`n"), $utf8)
        Invoke-Compile -Behavior $behavior -Name $name -InputFile $merged -Keep 'txt'
    }
}

# classical：装 domain 规则集装不下的东西（keyword / regexp / 进程名）
# 只出文本 —— mihomo v1.19.25 的 `convert-ruleset classical text` 必 panic
# （classical_strategy.go:57 nil 解引用），而且 panic 后退出码仍是 0。
# 文本版客户端直接用 `format: text` 读，行为一致。
$cls = Join-Path $Root 'build\classical'
if (Test-Path $cls) {
    $dst = Join-Path $Dist 'classical'
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Get-ChildItem $cls -Filter *.list | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $dst $_.Name) -Force
        '{0,-28} {1,8} N B (list, 不出 mrs)' -f "classical/$($_.BaseName)", $_.Length
    }
}

if ($fail) { Write-Host '构建失败：先修掉上面标出的行' -ForegroundColor Red; exit 1 }
Write-Host "产物目录：$Dist"
