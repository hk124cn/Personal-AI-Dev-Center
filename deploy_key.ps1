# ============================================================
# Dev Center - SSH 密钥部署脚本 (PowerShell)
#
# 作用：把**本机公钥**追加到各服务器的 ~/.ssh/authorized_keys，实现免密登录。
#
# ⚠️ 概念澄清（2026-10-27 用户提问「新增服务器是不是传密钥就行」）：
#   · **私钥永远留在本机**（config.json 的 key_path 指向本机文件），绝不上传到服务器。
#   · 上传到服务器的是**公钥**（`xxx.pub`），放进服务器的 ~/.ssh/authorized_keys。
#   · 本项目是「SSH 裸命令」架构（监控/同步/跑分/计划任务全是直接跑 shell 命令），
#     **不需要在远端安装任何 agent 或脚本**，公钥通了就全部功能自动可用。
#
# 用法：
#   .\deploy_key.ps1              # 部署到所有服务器（公钥已存在的自动跳过）
#   .\deploy_key.ps1 -Name 新加坡 # 只处理名字匹配的服务器
#   .\deploy_key.ps1 -DryRun      # 只看要做什么，不实际改
#   .\deploy_key.ps1 -Force       # 强制重写（忽略「已存在」检查）
# ============================================================
[CmdletBinding()]
param(
    [string]$Name = "",
    [switch]$DryRun,
    [switch]$Force,
    [switch]$Batch
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- 定位真实配置（**必须与后端读同一个文件**）----
# 运行时配置在 %APPDATA%\Personal AI Dev Center\config.json；
# 仓库根的 config.json 是旧结构的开发残留（服务器列表已过期），不要用。
$ConfigCandidates = @(
    (Join-Path $env:APPDATA "Personal AI Dev Center\config.json"),
    (Join-Path $ScriptDir "config.json")
)
$ConfigPath = $null
foreach ($c in $ConfigCandidates) {
    if (Test-Path $c) { $ConfigPath = $c; break }
}
if (-not $ConfigPath) {
    Write-Host "[!] 找不到配置文件，试过：" -ForegroundColor Red
    $ConfigCandidates | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    exit 1
}
Write-Host "配置文件: $ConfigPath" -ForegroundColor DarkGray

$config = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $config.servers -or $config.servers.Count -eq 0) {
    Write-Host "[!] 配置里没有服务器" -ForegroundColor Red
    exit 1
}

# ---- 字段兼容：新结构 host/port/user/key_path，旧结构 ip/sshPort/sshUser/sshKey ----
function Get-Field($server, [string[]]$names, $default = $null) {
    foreach ($n in $names) {
        if ($server.PSObject.Properties.Name -contains $n) {
            $v = $server.$n
            if ($v -ne $null -and "$v" -ne "") { return $v }
        }
    }
    return $default
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SSH 公钥部署（私钥留在本机，只上传公钥）" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$success = 0; $fail = 0; $skip = 0
foreach ($server in $config.servers) {
    $sName   = Get-Field $server @('name')
    $sshHost   = Get-Field $server @('host','ip')
    $port    = Get-Field $server @('port','sshPort') 22
    $user    = Get-Field $server @('user','sshUser')
    $keyPath = Get-Field $server @('key_path','sshKey')

    if ($Name -and $sName -notlike "*$Name*") { continue }

    Write-Host ""
    Write-Host ">>> $sName  ($user@$sshHost`:$port)" -ForegroundColor Cyan

    if (-not $keyPath) {
        Write-Host "    [SKIP] 未配置私钥路径（key_path）" -ForegroundColor Yellow
        $skip++; continue
    }
    $keyPath = [Environment]::ExpandEnvironmentVariables($keyPath)
    $keyPath = $keyPath -replace '^~', "$env:USERPROFILE"
    if (-not (Test-Path $keyPath)) {
        Write-Host "    [FAIL] 私钥不存在: $keyPath" -ForegroundColor Red
        $fail++; continue
    }
    $pubPath = "$keyPath.pub"
    if (-not (Test-Path $pubPath)) {
        # 从私钥导出公钥
        Write-Host "    ... 从私钥导出公钥" -ForegroundColor DarkGray
        & ssh-keygen -y -f $keyPath 2>$null | ForEach-Object { "$_ $sName" } | Set-Content -Path $pubPath -Encoding utf8
        if (-not (Test-Path $pubPath)) {
            Write-Host "    [FAIL] 无法导出公钥" -ForegroundColor Red
            $fail++; continue
        }
    }
    $PubKey = (Get-Content $pubPath -Raw -Encoding UTF8).Trim()
    $pubKeyData = ($PubKey -split '\s+')[1]

    if ($DryRun) {
        Write-Host "    [DRY] 将把公钥追加到 $user@$sshHost`:$port 的 authorized_keys" -ForegroundColor DarkGray
        Write-Host "    [DRY] 公钥指纹: $(& ssh-keygen -lf $pubPath 2>$null)" -ForegroundColor DarkGray
        continue
    }

    # 1) 先看公钥是否已存在
    $checkCmd = "grep -q '$pubKeyData' ~/.ssh/authorized_keys 2>/dev/null && echo '__KEY_EXISTS__'"
    # ⚠️ 必须带 -i + IdentitiesOnly：否则 ssh 会去试 agent 里的其它密钥，
    #    用错身份就认证失败（会把这台误判成"连不上"）。
    #    BatchMode=yes 保证不弹密码提示（纯免密检查；非交互环境会卡死等密码）。
    $check = & ssh -p $port -i $keyPath -o IdentitiesOnly=yes -o BatchMode=yes `
                    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 `
                    "$user@$sshHost" $checkCmd 2>&1
    $already = $false
    if ($LASTEXITCODE -ne 0) {
        # ⚠️ 要区分两种失败：
        #   · 「Permission denied / publickey」→ **主机是通的**，只是公钥还没部署
        #     → 这恰恰是要部署公钥的场景，继续往下走（改用密码方式写入）
        #   · 「Connection refused / timeout / No route」→ 主机根本连不上 → 真失败
        if ("$check" -match 'Permission denied|publickey|Authentication') {
            Write-Host "    ... 免密不通（公钥尚未部署），改用密码方式写入" -ForegroundColor Yellow
        } else {
            Write-Host "    [FAIL] 主机不可达" -ForegroundColor Red
            Write-Host "           错误: $check" -ForegroundColor DarkGray
            $fail++; continue
        }
    } else {
        $already = ("$check" -match '__KEY_EXISTS__')
    }
    if ($already -and -not $Force) {
        Write-Host "    [SKIP] 公钥已存在" -ForegroundColor DarkGray
    } else {
        $deployCmd = @"
mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && echo '$PubKey' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
"@
        # 先用免密试（公钥其实已存在、只是 check 步骤没带 -i 时会走到这）
        & ssh -p $port -i $keyPath -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 `
               "$user@$sshHost" $deployCmd 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    ... 免密不行，改用密码方式（需要你输一次密码）" -ForegroundColor Yellow
            if ($Batch) {
                Write-Host "    [FAIL] 免密不通，且 -Batch 模式不交互输密码（请在本机终端跑）" -ForegroundColor Red
                $fail++; continue
            }
            & ssh -p $port -o PreferredAuthentications=password -o PubkeyAuthentication=no `
                   -o ConnectTimeout=20 "$user@$sshHost" $deployCmd | Out-Null
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [FAIL] 写入 authorized_keys 失败" -ForegroundColor Red
            $fail++; continue
        }
        Write-Host "    [OK] 公钥已写入 authorized_keys" -ForegroundColor Green
    }

    # 2) 验证免密登录（**用 config 里配的那把私钥**，不是硬编码 <key>）
    $verify = & ssh -p $port -i $keyPath -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$user@$sshHost" "echo __OK__" 2>&1
    if ("$verify" -match '__OK__') {
        Write-Host "    [OK] 免密登录验证通过" -ForegroundColor Green
        $success++
    } else {
        Write-Host "    [FAIL] 免密登录验证失败: $verify" -ForegroundColor Red
        Write-Host "           检查远端: PubkeyAuthentication yes / ~/.ssh 权限 700 / authorized_keys 权限 600" -ForegroundColor DarkGray
        $fail++
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ("  成功 {0}   失败 {1}   跳过 {2}" -f $success, $fail, $skip) -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan
if ($fail -gt 0) { exit 1 }
exit 0
