@echo off
cd /d "C:\Users\nghia.n\OneDrive - COLLECTIUS SYSTEMS PTE. LTD\Documents\1.Personal\1.Learning\1. Practice\PersonaFinanceSystem"
REM Initialize Conda for this shell session
call C:\Users\nghia.n\AppData\Local\anaconda3\Scripts\activate.bat

REM Activate your specific conda environment
call conda activate spark-env

streamlit run system_v2.py
pause
