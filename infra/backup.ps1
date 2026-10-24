[CmdletBinding()]
param(
    [string]$OutputRoot,
    [string]$BackupName = (Get-Date -Format "yyyyMMdd-HHmmss")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ComposeFile = Join-Path $PSScriptRoot "docker-compose.yml"
$DataDirectory = Join-Path $ProjectRoot "data"
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $ProjectRoot "backups"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if ($BackupName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') {
    throw "BackupName 只能包含字母、数字、点、下划线或连字符，且最长 80 个字符"
}
$BackupDirectory = Join-Path $OutputRoot $BackupName
$BackupDirectory = [IO.Path]::GetFullPath($BackupDirectory)
if (-not $BackupDirectory.StartsWith($OutputRoot + [IO.Path]::DirectorySeparatorChar)) {
    throw "备份目录必须位于 OutputRoot 内"
}
$DatabaseDump = Join-Path $BackupDirectory "database.dump"
$DataArchive = Join-Path $BackupDirectory "data.tar"
$ManifestPath = Join-Path $BackupDirectory "manifest.json"
$ContainerDump = "/tmp/bagu-p4-backup.dump"

function Invoke-DockerCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 命令失败（退出码 $LASTEXITCODE）：$($Arguments -join ' ')"
    }
}

if (Test-Path -LiteralPath $BackupDirectory) {
    throw "备份目录已存在，不会覆盖：$BackupDirectory"
}

New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$succeeded = $false
$servicesToResume = @()
$resumeFailed = $false
try {
    $activeTaskCount = @(& docker compose -f $ComposeFile exec -T postgres sh -lc `
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT (SELECT count(*) FROM ingestion_jobs WHERE status IN (''QUEUED'',''PROCESSING'')) + (SELECT count(*) FROM question_extraction_runs WHERE status IN (''QUEUED'',''PROCESSING'')) + (SELECT count(*) FROM user_profiles WHERE extraction_status = ''PROCESSING'');"')
    if ($LASTEXITCODE -ne 0 -or $activeTaskCount.Count -ne 1 -or $activeTaskCount[0].Trim() -notmatch '^\d+$') {
        throw "无法确认后台任务是否空闲，已停止备份"
    }
    if ([int]$activeTaskCount[0].Trim() -gt 0) {
        throw "仍有解析、题目整理或画像任务正在排队/处理，请等待任务结束后再备份"
    }

    $runningServices = @(& docker compose -f $ComposeFile ps --services --filter status=running)
    if ($LASTEXITCODE -ne 0) { throw "无法读取 Compose 运行状态" }
    $servicesToResume = @("api", "worker", "web" | Where-Object { $runningServices -contains $_ })
    if ($servicesToResume.Count -gt 0) {
        Write-Host "[1/5] 短暂停止写入服务以获得一致快照..."
        Invoke-DockerCompose -Arguments (@("stop") + $servicesToResume)
    }

    Write-Host "[2/5] 导出 PostgreSQL..."
    Invoke-DockerCompose exec -T postgres sh -lc `
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl -f /tmp/bagu-p4-backup.dump'
    Invoke-DockerCompose cp "postgres:$ContainerDump" $DatabaseDump

    Write-Host "[3/5] 归档 data/ 私有原件..."
    if (-not (Test-Path -LiteralPath $DataDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
    }
    & tar --format=ustar -cf $DataArchive -C $DataDirectory .
    if ($LASTEXITCODE -ne 0) {
        throw "data/ 归档失败（tar 退出码 $LASTEXITCODE）"
    }

    $gitCommit = $null
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $gitCommit = (& git -C $ProjectRoot rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -ne 0) { $gitCommit = $null }
    }
    $dataFiles = @(Get-ChildItem -LiteralPath $DataDirectory -Recurse -File -Force)
    $manifest = [ordered]@{
        formatVersion = 1
        createdAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        application = "personalbagu"
        gitCommit = $gitCommit
        contents = [ordered]@{
            database = "PostgreSQL custom dump"
            dataDirectory = "data.tar"
            redis = "not-backed-up; transient task queue only"
            secrets = "not-included"
        }
        sourceStats = [ordered]@{
            dataFileCount = $dataFiles.Count
            dataBytes = [long](($dataFiles | Measure-Object -Property Length -Sum).Sum ?? 0)
        }
        files = @(
            [ordered]@{
                name = "database.dump"
                bytes = (Get-Item -LiteralPath $DatabaseDump).Length
                sha256 = (Get-FileHash -LiteralPath $DatabaseDump -Algorithm SHA256).Hash.ToLowerInvariant()
            },
            [ordered]@{
                name = "data.tar"
                bytes = (Get-Item -LiteralPath $DataArchive).Length
                sha256 = (Get-FileHash -LiteralPath $DataArchive -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        )
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding utf8NoBOM

    Write-Host "[4/5] 校验文件哈希和归档路径..."
    & (Join-Path $PSScriptRoot "verify-backup.ps1") -BackupPath $BackupDirectory -ManifestOnly
    if ($LASTEXITCODE -ne 0) {
        throw "备份校验失败"
    }

    Write-Host "[5/5] 备份完成：$BackupDirectory"
    $succeeded = $true
    Write-Output $BackupDirectory
}
finally {
    & docker compose -f $ComposeFile exec -T postgres rm -f $ContainerDump 2>$null
    if ($servicesToResume.Count -gt 0) {
        & docker compose -f $ComposeFile up -d @servicesToResume
        if ($LASTEXITCODE -ne 0) {
            $resumeFailed = $true
        }
    }
    if (-not $succeeded -and (Test-Path -LiteralPath $BackupDirectory)) {
        Remove-Item -LiteralPath $BackupDirectory -Recurse -Force
    }
    if ($resumeFailed) {
        throw "备份流程结束，但写入服务未能自动恢复，请运行 docker compose -f infra/docker-compose.yml up -d"
    }
}
