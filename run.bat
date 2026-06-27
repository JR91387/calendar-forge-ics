@echo off
setlocal

set SCRIPT_DIR=%~dp0

:: Find Python >= 3.8
set PYTHON=
for %%C in (py python3 python) do (
    if not defined PYTHON (
        where %%C >nul 2>&1 && (
            for /f "delims=" %%V in ('%%C -c "import sys; print(sys.version_info >= (3,8))" 2^>nul') do (
                if "%%V"=="True" set PYTHON=%%C
            )
        )
    )
)

if not defined PYTHON (
    echo ERROR: Python 3.8 or later not found.
    echo Install from https://www.python.org/downloads/ then re-run.
    pause
    exit /b 1
)

"%PYTHON%" "%SCRIPT_DIR%03_App\icsscrub.py"
if errorlevel 1 (
    echo.
    echo ERROR: Calendar Forge exited with an error. See above.
    pause
)
