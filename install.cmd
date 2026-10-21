@echo off
rem Установка Polytome: своё окружение Python + команда polytome.
rem Нужен Python 3.10+ (https://www.python.org/downloads/ — галочка "Add to PATH").
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
  echo Создаю окружение .venv ...
  %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -e . || goto :fail
echo.
echo Готово. Команда: "%~dp0.venv\Scripts\polytome.exe"
echo Чтобы писать просто polytome — добавьте в PATH папку: %~dp0.venv\Scripts
echo Дальше: polytome setup   (выбор провайдера и моделей)
exit /b 0
:fail
echo Установка не удалась.
exit /b 1
