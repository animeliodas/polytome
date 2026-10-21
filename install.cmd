@echo off
rem Установка Hanru: своё окружение Python + команда hanru.
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
echo Готово. Команда: "%~dp0.venv\Scripts\hanru.exe"
echo Чтобы писать просто hanru — добавьте в PATH папку: %~dp0.venv\Scripts
echo Дальше: hanru setup   (выбор провайдера и моделей)
exit /b 0
:fail
echo Установка не удалась.
exit /b 1
