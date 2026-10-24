[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [switch]$ManifestOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ComposeFile = Join-Path $PSScriptRoot "docker-compose.yml"
$BackupDirectory = [IO.Path]::GetFullPath($BackupPath)
$ManifestPath = Join-Path $BackupDirectory "manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "缺少 manifest.json：$BackupDirectory"
}

$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($manifest.formatVersion -ne 1 -or $manifest.application -ne "personalbagu") {
    throw "不支持的备份格式或应用标识"
}

$expectedFiles = @("database.dump", "data.tar")
foreach ($name in $expectedFiles) {
    $entry = @($manifest.files | Where-Object { $_.name -eq $name })
    if ($entry.Count -ne 1) {
        throw "manifest 中必须且只能有一个 $name 条目"
    }
    $path = Join-Path $BackupDirectory $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "备份文件缺失：$name"
    }
    $actualLength = (Get-Item -LiteralPath $path).Length
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualLength -ne [long]$entry[0].bytes -or $actualHash -ne $entry[0].sha256) {
        throw "备份文件校验失败：$name"
    }
}

$DataArchive = Join-Path $BackupDirectory "data.tar"
$archiveEntries = @(& tar -tf $DataArchive)
if ($LASTEXITCODE -ne 0) {
    throw "无法读取 data.tar"
}
foreach ($entry in $archiveEntries) {
    $normalized = $entry.Replace("\", "/")
    if (
        $normalized.StartsWith("/") -or
        $normalized -match '^[A-Za-z]:' -or
        $normalized -match '(^|/)\.\.(/|$)'
    ) {
        throw "data.tar 包含不安全路径：$entry"
    }
}
$verboseArchiveEntries = @(& tar -tvf $DataArchive)
if ($LASTEXITCODE -ne 0) {
    throw "无法检查 data.tar 的成员类型"
}
foreach ($entry in $verboseArchiveEntries) {
    if ($entry -notmatch '^[-d]') {
        throw "data.tar 包含不支持的链接或特殊文件：$entry"
    }
}

if ($ManifestOnly) {
    Write-Host "备份清单、SHA-256 和归档路径校验通过。"
    exit 0
}

function Invoke-DockerCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 命令失败（退出码 $LASTEXITCODE）：$($Arguments -join ' ')"
    }
}

$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$VerifyDatabase = "bagu_verify_$suffix"
$ContainerDump = "/tmp/bagu-verify-$suffix.dump"
$extractDirectory = Join-Path ([IO.Path]::GetTempPath()) "bagu-verify-$suffix"
$databaseCreated = $false
try {
    Write-Host "[1/2] 在临时数据库中验证 PostgreSQL dump..."
    Invoke-DockerCompose cp (Join-Path $BackupDirectory "database.dump") "postgres:$ContainerDump"
    Invoke-DockerCompose exec -T postgres sh -lc `
        "createdb -U `"`$POSTGRES_USER`" -O `"`$POSTGRES_USER`" $VerifyDatabase"
    $databaseCreated = $true
    Invoke-DockerCompose exec -T postgres sh -lc `
        "pg_restore --exit-on-error --no-owner --no-acl -U `"`$POSTGRES_USER`" -d $VerifyDatabase $ContainerDump"
    Invoke-DockerCompose exec -T postgres sh -lc `
        "psql -v ON_ERROR_STOP=1 -U `"`$POSTGRES_USER`" -d $VerifyDatabase -Atqc 'SELECT version_num FROM alembic_version LIMIT 1'"

    Write-Host "[2/2] 在临时目录中验证 data.tar..."
    New-Item -ItemType Directory -Path $extractDirectory -Force | Out-Null
    & tar -xf $DataArchive -C $extractDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "data.tar 临时解压失败"
    }
    $restoredFiles = @(Get-ChildItem -LiteralPath $extractDirectory -Recurse -File -Force)
    $restoredBytes = [long](($restoredFiles | Measure-Object -Property Length -Sum).Sum ?? 0)
    if (
        $restoredFiles.Count -ne [int]$manifest.sourceStats.dataFileCount -or
        $restoredBytes -ne [long]$manifest.sourceStats.dataBytes
    ) {
        throw "data.tar 解压后的文件数量或总字节数与 manifest 不一致"
    }
    Write-Host "完整备份验证通过：数据库可恢复，data/ 共 $($restoredFiles.Count) 个文件。"
}
finally {
    if ($databaseCreated) {
        & docker compose -f $ComposeFile exec -T postgres sh -lc `
            "dropdb --if-exists -U `"`$POSTGRES_USER`" $VerifyDatabase" 2>$null
    }
    & docker compose -f $ComposeFile exec -T postgres rm -f $ContainerDump 2>$null
    if (Test-Path -LiteralPath $extractDirectory) {
        Remove-Item -LiteralPath $extractDirectory -Recurse -Force
    }
}
