@echo off
REM Arranque da app de teste — Paraíso Villas & Spa
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python nao encontrado no PATH. Instale Python 3.11+ de python.org.
    pause
    exit /b 1
)

python -m rececionista
if errorlevel 1 pause
