@echo off
setlocal

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if not exist ".venv\Scripts\activate.bat" (
    echo Local virtual environment not found at ".venv\Scripts\activate.bat".
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
streamlit run system_v2.py

endlocal
