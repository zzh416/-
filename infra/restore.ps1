[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [switch]$ConfirmRestore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ConfirmRestore) {
    throw "恢复会替换当前数据库和 data/。确认后请重新运行并添加 -ConfirmRestore。"
}

$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ComposeFile = Join-Path $PSScriptRoot "docker-compose.yml"
$DataDirectory = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "data"))
$ExpectedDataDirectory = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "data"))
if ($DataDirectory -ne $ExpectedDataDirectory -or $DataDirectory -eq $ProjectRoot) {
    throw "拒绝操作未通过安全检查的数据目录：$DataDirectory"
}
$BackupDirectory = [IO.Path]::GetFullPath($BackupPath)

function Invoke-DockerCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 命令失败（退出码 $LASTEXITCODE）：$($Arguments -join ' ')"
    }
}

Write-Host "[1/6] 完整验证待恢复备份..."
& (Join-Path $PSScriptRoot "verify-backup.ps1") -BackupPath $BackupDirectory
if ($LASTEXITCODE -ne 0) { throw "待恢复备份验证失败" }

Write-Host "[2/6] 自动备份当前数据作为回退点..."
$SafetyRoot = Join-Path $ProjectRoot "backups\pre-restore"
$SafetyName = Get-Date -Format "yyyyMMdd-HHmmss-fff"
& (Join-Path $PSScriptRoot "backup.ps1") -OutputRoot $SafetyRoot -BackupName $SafetyName | Out-Host
if ($LASTEXITCODE -ne 0) { throw "恢复前安全备份失败，已停止恢复" }

$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$RestoreDatabase = "bagu_restore_$suffix"
$PreviousDatabase = "bagu_previous_$suffix"
$ContainerDump = "/tmp/bagu-restore-$suffix.dump"
$StagingDirectory = Join-Path $ProjectRoot ".restore-staging-$suffix"
$PreviousDataDirectory = Join-Path $ProjectRoot ".restore-previous-$suffix"
$databaseSwapped = $false
$previousDatabaseRenamed = $false
$dataSwapped = $false
$previousDataMoved = $false
$DatabaseUser = $null
$DatabaseName = $null

try {
    Write-Host "[3/6] 将备份恢复到临时数据库和临时目录..."
    Invoke-DockerCompose cp (Join-Path $BackupDirectory "database.dump") "postgres:$ContainerDump"
    Invoke-DockerCompose exec -T postgres sh -lc `
        "createdb -U `"`$POSTGRES_USER`" -O `"`$POSTGRES_USER`" $RestoreDatabase"
    Invoke-DockerCompose exec -T postgres sh -lc `
        "pg_restore --exit-on-error --no-owner --no-acl -U `"`$POSTGRES_USER`" -d $RestoreDatabase $ContainerDump"

    New-Item -ItemType Directory -Path $StagingDirectory -Force | Out-Null
    & tar -xf (Join-Path $BackupDirectory "data.tar") -C $StagingDirectory
    if ($LASTEXITCODE -ne 0) { throw "data.tar 解压失败" }

    $identity = @(& docker compose -f $ComposeFile exec -T postgres sh -lc `
        'printf "%s\n%s\n" "$POSTGRES_USER" "$POSTGRES_DB"')
    if ($LASTEXITCODE -ne 0 -or $identity.Count -lt 2) {
        throw "无法读取 PostgreSQL 运行配置"
    }
    $DatabaseUser = $identity[0].Trim()
    $DatabaseName = $identity[1].Trim()
    if ($DatabaseUser -notmatch '^[A-Za-z0-9_]+$' -or $DatabaseName -notmatch '^[A-Za-z0-9_]+$') {
        throw "数据库用户或数据库名包含恢复脚本不支持的字符"
    }
    if ($DatabaseName -in @("postgres", "template0", "template1")) {
        throw "拒绝替换 PostgreSQL 系统数据库：$DatabaseName"
    }

    Write-Host "[4/6] 停止应用并原子切换数据库与 data/..."
    Invoke-DockerCompose stop api worker web
    Invoke-DockerCompose rm -f api worker web
    Invoke-DockerCompose -Arguments @(
        "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
        "-U", $DatabaseUser, "-d", "postgres", "-c",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DatabaseName' AND pid <> pg_backend_pid();"
    )
    Invoke-DockerCompose -Arguments @(
        "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
        "-U", $DatabaseUser, "-d", "postgres", "-c",
        "ALTER DATABASE `"$DatabaseName`" RENAME TO `"$PreviousDatabase`";"
    )
    $previousDatabaseRenamed = $true
    Invoke-DockerCompose -Arguments @(
        "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
        "-U", $DatabaseUser, "-d", "postgres", "-c",
        "ALTER DATABASE `"$RestoreDatabase`" RENAME TO `"$DatabaseName`";"
    )
    $databaseSwapped = $true

    if (Test-Path -LiteralPath $DataDirectory) {
        Move-Item -LiteralPath $DataDirectory -Destination $PreviousDataDirectory
        $previousDataMoved = $true
    }
    Move-Item -LiteralPath $StagingDirectory -Destination $DataDirectory
    $dataSwapped = $true
    Invoke-DockerCompose exec -T redis redis-cli FLUSHDB | Out-Null

    Write-Host "[5/6] 启动应用并检查就绪状态..."
    Invoke-DockerCompose -Arguments @("up", "-d", "api", "worker", "web")
    $ready = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9000/api/v1/health/ready" -TimeoutSec 3
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        }
        catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "恢复后的 API 未在 30 秒内就绪" }

    Write-Host "[6/6] 清理旧副本..."
    Invoke-DockerCompose exec -T postgres dropdb --if-exists -U $DatabaseUser $PreviousDatabase
    if (Test-Path -LiteralPath $PreviousDataDirectory) {
        Remove-Item -LiteralPath $PreviousDataDirectory -Recurse -Force
    }
    Write-Host "恢复完成。恢复前安全备份位于：$(Join-Path $SafetyRoot $SafetyName)"
}
catch {
    Write-Warning "恢复未完成：$($_.Exception.Message)"
    if ($previousDatabaseRenamed) {
        & docker compose -f $ComposeFile stop api worker web 2>$null
        if ($databaseSwapped) {
            & docker compose -f $ComposeFile exec -T postgres psql -U $DatabaseUser -d postgres -c `
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DatabaseName' AND pid <> pg_backend_pid();" 2>$null
            & docker compose -f $ComposeFile exec -T postgres psql -U $DatabaseUser -d postgres -c `
                "ALTER DATABASE `"$DatabaseName`" RENAME TO `"$RestoreDatabase`";" 2>$null
        }
        & docker compose -f $ComposeFile exec -T postgres psql -U $DatabaseUser -d postgres -c `
            "ALTER DATABASE `"$PreviousDatabase`" RENAME TO `"$DatabaseName`";" 2>$null
    }
    if ($previousDataMoved -and (Test-Path -LiteralPath $PreviousDataDirectory)) {
        if ($dataSwapped -and (Test-Path -LiteralPath $DataDirectory)) {
            Remove-Item -LiteralPath $DataDirectory -Recurse -Force
        }
        Move-Item -LiteralPath $PreviousDataDirectory -Destination $DataDirectory
    }
    & docker compose -f $ComposeFile up -d api worker web 2>$null
    throw
}
finally {
    & docker compose -f $ComposeFile exec -T postgres rm -f $ContainerDump 2>$null
    if (-not [string]::IsNullOrWhiteSpace($DatabaseUser)) {
        & docker compose -f $ComposeFile exec -T postgres dropdb --if-exists -U $DatabaseUser $RestoreDatabase 2>$null
    }
    if (Test-Path -LiteralPath $StagingDirectory) {
        Remove-Item -LiteralPath $StagingDirectory -Recurse -Force
    }
}
