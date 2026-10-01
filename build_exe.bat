@echo off
rem Genera "EDCNet Alinity.exe" en esta carpeta, junto a config.json y
rem metadata_edcnet.json (el programa guarda su estado al lado del .exe).
cd /d "%~dp0"

python -m pip install --upgrade -r requirements-build.txt || goto :error
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "EDCNet Alinity" --icon icono.ico --add-data "icono.ico;." ^
    alinity_edcnet_gui.py || goto :error

copy /y "dist\EDCNet Alinity.exe" . >nul || goto :error
rmdir /s /q build dist 2>nul
del /q "EDCNet Alinity.spec" 2>nul
echo.
echo Listo: "%~dp0EDCNet Alinity.exe"
echo Para el acceso directo en el escritorio:  powershell -ExecutionPolicy Bypass -File crear_acceso_directo.ps1
goto :eof

:error
echo.
echo ERROR al generar el ejecutable.
exit /b 1
