@echo off
rem Double-click to open Math Tutor Studio in your browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run app.py
