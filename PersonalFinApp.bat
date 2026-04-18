@echo off
setlocal

cd /d "%~dp0"

call ".venv\Scripts\activate.bat"
streamlit run system_v2.py

pause