@echo off
chcp 65001 >nul 2>&1
title Cockpit Frontend

cd /d "%~dp0"

echo ========================================
echo   DesayMem Cockpit Frontend
echo ========================================
echo.

REM ── Check Python ──
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

REM ── Check .env, create from .env.example if missing ──
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] .env not found. Copying from .env.example...
        copy /y ".env.example" ".env" >nul
    ) else (
        echo [INFO] .env not found. Creating with defaults...
        (
            echo LLM_API_KEY=请填写与服务器vLLM一致的API_Key
            echo LLM_MODEL=memory-llm
            echo LLM_BASE_URL=http://10.133.72.161:20140/v1
            echo PROXY_PORT=8767
        ) > .env
    )
    echo [WARNING] .env created. Please edit it with the correct API Key.
    echo.
    type .env
    echo.
    pause
)

REM ── Check API Key is not placeholder ──
findstr /c:"请填写" .env >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [WARNING] LLM_API_KEY in .env is still placeholder!
    echo [INFO] Please edit .env with the real API Key.
    echo.
    pause
)

REM ── Install deps if needed ──
pip show fastapi >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
)

REM ── Check port 8767 (LLM proxy) ──
netstat -ano | findstr ":8767 " | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Starting LLM proxy on port 8767...
    start /min "LLM Proxy" python llm_proxy.py
    timeout /t 3 /nobreak >nul
) else (
    echo [INFO] Port 8767 already in use, skipping LLM proxy start.
)

REM ── Check port 8080 (static server) ──
netstat -ano | findstr ":8080 " | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Starting static file server on port 8080...
    start /min "Frontend Server" python -m http.server 8080 --bind 127.0.0.1
    timeout /t 2 /nobreak >nul
) else (
    echo [INFO] Port 8080 already in use, skipping static server start.
)

REM ── Open browser ──
echo [INFO] Opening browser...
start "" "http://127.0.0.1:8080"

echo.
echo ========================================
echo   Frontend:       http://127.0.0.1:8080
echo   LLM Proxy:      http://127.0.0.1:8767
echo   Memory Backend: http://10.133.72.161:20142
echo   LLM Server:     http://10.133.72.161:20140/v1
echo ========================================
echo.
echo Press Ctrl+C to stop the static server.
echo Close the "LLM Proxy" window to stop the proxy.
echo.
pause
