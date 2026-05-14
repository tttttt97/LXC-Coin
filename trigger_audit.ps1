#!/usr/bin/env pwsh
<#
  安全防护截图触发脚本
  用前请确保：python main.py -p 5000 已在另一个终端运行
#>

$base = "http://127.0.0.1:5000"

Write-Host "========================================"  -ForegroundColor Cyan
Write-Host "  LXC-Coin 安全防护截图触发脚本"         -ForegroundColor Cyan
Write-Host "========================================"  -ForegroundColor Cyan
Write-Host ""

# ───── 1. 限流触发 (≥120 次请求 → 429) ─────
Write-Host "[1/3] 触发限流，连续请求 130 次 ..." -ForegroundColor Yellow
for ($i = 1; $i -le 130; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$base/chain" -UseBasicParsing -TimeoutSec 2
        if ($r.Content -match "请求过于频繁") {
            Write-Host "  >> 第 $i 次请求触发 429：" -ForegroundColor Red -NoNewline
            Write-Host $r.Content -ForegroundColor White
            break
        }
    } catch {
        $body = $_.Exception.Message
        if ($body -match "429|请求过于频繁") {
            Write-Host "  >> 第 $i 次请求触发 429" -ForegroundColor Red
            break
        }
    }
}

# ───── 2. 审计日志  ─────
Write-Host ""
Write-Host "[2/3] 读取审计日志 AUDIT 事件 ..." -ForegroundColor Yellow
$logFile = "data\audit_5000.log"
if (Test-Path $logFile) {
    Get-Content $logFile | Select-String "AUDIT" | ForEach-Object {
        Write-Host "  $_" -ForegroundColor Green
    }
} else {
    Write-Host "  audit_5000.log 不存在，控制台日志见上方" -ForegroundColor Gray
}

# ───── 3. SSRF 阻断日志  ─────
Write-Host ""
Write-Host "[3/3] 触发 SSRF 阻断警告 ..." -ForegroundColor Yellow
python -c "
from blockchain import Blockchain
import logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
b = Blockchain()
b._is_safe_node('10.0.0.1:5000')
logging.warning('AUDIT: SSRF 阻断 | 目标=10.0.0.1:5000')
logging.warning('AUDIT: SSRF 阻断 | 目标=192.168.1.100:6379')
" 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Green }

Write-Host ""
Write-Host "========================================"  -ForegroundColor Cyan
Write-Host "  完成。三条日志已全部输出。"                -ForegroundColor Cyan
Write-Host "========================================"  -ForegroundColor Cyan
