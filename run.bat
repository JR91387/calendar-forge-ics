@echo off
setlocal

set SCRIPT_DIR=%~dp0
set VENV=%SCRIPT_DIR%.venv

:: Find Python >= 3.8  (try Windows py launcher first, then python3, then python)
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

:: Create venv once
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating virtual environment...
    %PYTHON% -m venv "%VENV%"
    "%VENV%\Scripts\pip" install --quiet -r "%SCRIPT_DIR%requirements.txt"
)

"%VENV%\Scripts\python" "%SCRIPT_DIR%icsscrub.py"
