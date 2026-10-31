@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Rececionista Virtual - Paraiso Villas ^& Spa

REM ---------------------------------------------------------------------------
REM Escolha do interpretador Python.
REM
REM O primeiro python.exe do PATH pode ser um Python embutido de outra
REM aplicacao (por exemplo o LibreOffice Portable, que traz Python 3.8 sem
REM Tkinter). Testamos cada candidato e so aceitamos quem cumpra os dois
REM requisitos da app: versao 3.10 ou superior E modulo tkinter disponivel.
REM ---------------------------------------------------------------------------

set "PYRUN="
set "CHECK=import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"

call :try "py" -3
if not defined PYRUN call :try "python"
if not defined PYRUN call :try "python3"
if not defined PYRUN call :try "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PYRUN call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYRUN call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYRUN call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYRUN call :try "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYRUN call :try "%ProgramFiles%\Python313\python.exe"
if not defined PYRUN call :try "%ProgramFiles%\Python312\python.exe"
if not defined PYRUN call :try "%ProgramFiles%\Python311\python.exe"

if not defined PYRUN (
    echo.
    echo ============================================================
    echo  Python 3.10 ou superior com Tkinter nao foi encontrado.
    echo ============================================================
    echo.
    echo O "python" que aparece primeiro no PATH e:
    where python
    echo.
    echo Se esse for o Python do LibreOffice/XAMPP, nao serve: e antigo e
    echo vem sem Tkinter, que e a biblioteca grafica da interface.
    echo.
    echo Instale o Python oficial em https://www.python.org/downloads/windows/
    echo e, no instalador, deixe marcadas as opcoes:
    echo    - Add python.exe to PATH
    echo    - tcl/tk and IDLE
    echo.
    pause
    exit /b 1
)

echo A usar: %PYRUN%
%PYRUN% -m rececionista
if errorlevel 1 (
    echo.
    echo A aplicacao terminou com erro. Veja a mensagem acima.
    pause
)
exit /b 0

REM Valida um candidato: versao 3.10+ e tkinter importavel.
:try
%~1 %~2 -c "%CHECK%" >nul 2>nul
if not errorlevel 1 set "PYRUN="%~1" %~2"
goto :eof
