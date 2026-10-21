@echo off
rem Запуск Hanru: hanru.cmd [папка книги] [-p "задача"]
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
"%~dp0.venv\Scripts\python.exe" -m hanru %*
