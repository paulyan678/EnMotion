@echo off
cd /d "%~dp0"

if not exist "node_modules" (
    echo [setup] Installing root dependencies...
    npm ci
    if errorlevel 1 exit /b %errorlevel%
)

npm run dev
