# ============================================================
# Dev Center - SSH 密钥部署脚本 (PowerShell)
# 用法: .\deploy_key.ps1
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$KeyPath = "$env:USERPROFILE\.ssh\dev_center"
$PubKeyPath = "$KeyPath.pub"
$ConfigPath = Join-Path $ScriptDir "config.json"

# ── 检查公钥是否存在 ──────────────────────────────────────────
if (-not (Test-Path $PubKeyPath)) {
    Write-Host "[!] 公钥不存在: $PubKeyPath" -ForegroundColor Red
    Write-Host "    请先生成: ssh-keygen -t ed25519 -C 'dev-center@win11' -f $KeyPath -N ''''"
    exit 1
}

$PubKey = (Get-Content $PubKeyPath -Raw).Trim()
Write-Host "公钥内容:" -ForegroundColor Cyan
Write-Host $PubKey
Write-Host "============================================================"

# ── 加载服务器配置 ──────────────────────────────────────────────
if (-not (Test-Path $ConfigPath)) {
    Write-Host "[!] 配置文件不存在: $ConfigPath" -ForegroundColor Red
    exit 1
}

$config = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json

if (-not $config.servers -or $config.servers.Count -eq 0) {
    Write-Host "[!] 配置文件中没有找到服务器列表" -ForegroundColor Red
    exit 1
}

# ── 开始部署 ───────────────────────────────────────────────────
Write-Host "`n开始部署 SSH 密钥到各服务器..." -ForegroundColor Yellow
Write-Host "（每个服务器需要输入一次密码）`n"

$successCount = 0
$failCount    = 0

foreach ($server in $config.servers) {
    $host_ = $server.host
    $port  = $server.port
    $user  = $server.user
    $name  = $server.name

    Write-Host ">>> 部署到 $name ($user@$host_`:$port) ..." -ForegroundColor Cyan

    # 先检查公钥是否已经存在于 authorized_keys（避免重复追加）
    # 提取公钥的 key data 部分（去掉注释）用于精确匹配
    $pubKeyData = ($PubKey -split '\s+')[1]

    $checkCmd = @"
if grep -q '$pubKeyData' ~/.ssh/authorized_keys 2>/dev/null; then echo '__KEY_EXISTS__'; fi
"@

    $checkResult = ssh -p $port -o StrictHostKeyChecking=accept-new "$user@$host_" $checkCmd 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    [FAIL] $name 无法连接（请检查网络/SSH 服务/密码）" -ForegroundColor Red
        $failCount++
        Write-Host ""
        continue
    }

    if ($checkResult -match '__KEY_EXISTS__') {
        Write-Host "    [SKIP] $name 公钥已存在，跳过部署" -ForegroundColor Yellow
        # 仍然验证免密登录
        $verifyCmd = "echo '    [OK] 免密登录验证通过'"
        ssh -p $port -i $KeyPath -o BatchMode=yes -o ConnectTimeout=5 "$user@$host_" $verifyCmd 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [!] 免密登录验证失败，请检查服务器端配置" -ForegroundColor Yellow
        }
        $successCount++
        Write-Host ""
        continue
    }

    # 使用 ssh 创建 .ssh 目录并追加公钥
    $deployCmd = @"
mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '$PubKey' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys
"@

    ssh -p $port "$user@$host_" $deployCmd

    if ($LASTEXITCODE -eq 0) {
        Write-Host "    [OK] $name 密钥部署成功" -ForegroundColor Green

        # 验证免密登录
        $verifyCmd = "echo '    [OK] 免密登录验证通过'"
        ssh -p $port -i $KeyPath -o BatchMode=yes -o ConnectTimeout=5 "$user@$host_" $verifyCmd 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [!] 免密登录验证失败，请检查服务器端 sshd 配置" -ForegroundColor Yellow
            Write-Host "        确认以下选项已启用: PubkeyAuthentication yes" -ForegroundColor Yellow
        } else {
            $successCount++
        }
    } else {
        Write-Host "    [FAIL] $name 部署失败" -ForegroundColor Red
        $failCount++
    }

    Write-Host ""
}

# ── 部署结果汇总 ───────────────────────────────────────────────
Write-Host "============================================================"
Write-Host "部署完成！" -ForegroundColor Green
Write-Host "  成功: $successCount   失败: $failCount   总计: $($config.servers.Count)" -ForegroundColor White
Write-Host ""
Write-Host "测试命令:" -ForegroundColor Green
foreach ($server in $config.servers) {
    Write-Host "  ssh -i $KeyPath $($server.user)@$($server.host)" -ForegroundColor White
}
Write-Host ""

if ($failCount -gt 0) {
    exit 1
}
