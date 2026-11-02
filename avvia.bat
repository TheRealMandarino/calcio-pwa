@echo off
title Calcio PWA - Server & Browser
color 0A

cd /d "%~dp0"

echo Avvio del server locale in corso...
if exist "venv\Scripts\python.exe" (
    start /b "" "venv\Scripts\python.exe" backend\server.py
) else (
    start /b "" python backend\server.py
)

:: Attende un secondo che il server sia attivo
timeout /t 1 /nobreak > nul

echo Apertura della PWA su Google Chrome...
start chrome "http://localhost:8000/docs/"

exit