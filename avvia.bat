@echo off
title Calcio PWA - Server & Browser
color 0A

echo Avvio del server locale in corso...
start /b python -m http.server 8000

:: Attende un secondo che il server sia attivo
timeout /t 1 /nobreak > nul

echo Apertura della PWA su Google Chrome...
start chrome "http://localhost:8000/docs/"

exit