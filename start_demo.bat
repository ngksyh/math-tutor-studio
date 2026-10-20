@echo off
rem Double-click to open Math Tutor Studio with sample data (data\demo.db). Your own data is not used.
cd /d "%~dp0"
set MTS_DEMO=1
".venv\Scripts\python.exe" -m streamlit run app.py
