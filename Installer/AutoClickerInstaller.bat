@echo off
setlocal

title Auto Clicker Installer

echo =====================================
echo      Auto Clicker Installer
echo =====================================
echo.
pause

:: ===== 路徑設定 =====
set INSTALL_DIR=%USERPROFILE%\autoclicker
set EXE_PATH=%INSTALL_DIR%\autoclicker.exe

:: ===== 取得 Desktop（支援自訂路徑）=====
for /f "delims=" %%i in ('powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"') do set DESKTOP=%%i

:: ===== 取得 Start Menu =====
for /f "delims=" %%i in ('powershell -NoProfile -Command "[Environment]::GetFolderPath('Programs')"') do set STARTMENU=%%i

set APPFOLDER=%STARTMENU%\AutoClicker
set DESKTOP_SHORTCUT=%DESKTOP%\AutoClicker.lnk
set START_SHORTCUT=%APPFOLDER%\AutoClicker.lnk
set UNINSTALL_SHORTCUT=%APPFOLDER%\Uninstall AutoClicker.lnk

:: ===== 建立資料夾 =====
if not exist "%INSTALL_DIR%" (
    echo Creating install directory...
    mkdir "%INSTALL_DIR%"
)

if not exist "%APPFOLDER%" (
    echo Creating Start Menu folder...
    mkdir "%APPFOLDER%"
)

cd /d "%INSTALL_DIR%"

:: ===== 檢查 curl =====
where curl >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: curl not found.
    pause
    exit /b
)

:: ===== 下載 =====
echo Downloading Auto Clicker...
curl -L -o "autoclicker.exe" ^
https://twds.dl.sourceforge.net/project/orphamielautoclicker/AutoClicker.exe

if not exist "%EXE_PATH%" (
    echo ERROR: Download failed!
    pause
    exit /b
)

echo Download complete.

:: ===== 建立捷徑函數（用 PowerShell）=====
echo Creating shortcuts...

powershell -NoProfile -Command ^
"$ws = New-Object -ComObject WScript.Shell; ^
$s = $ws.CreateShortcut('%DESKTOP_SHORTCUT%'); ^
$s.TargetPath = '%EXE_PATH%'; ^
$s.Save(); ^
$s2 = $ws.CreateShortcut('%START_SHORTCUT%'); ^
$s2.TargetPath = '%EXE_PATH%'; ^
$s2.Save()"

:: ===== 建立卸載器 =====
echo Creating uninstaller...

(
echo @echo off
echo title Auto Clicker Uninstaller
echo echo Uninstall Auto Clicker?
echo pause
echo rmdir /S /Q "%INSTALL_DIR%"
echo rmdir /S /Q "%APPFOLDER%"
echo del "%DESKTOP_SHORTCUT%"
echo echo Uninstalled.
echo pause
) > "%INSTALL_DIR%\uninstall.bat"

:: ===== 建立卸載捷徑 =====
powershell -NoProfile -Command ^
"$ws = New-Object -ComObject WScript.Shell; ^
$s = $ws.CreateShortcut('%UNINSTALL_SHORTCUT%'); ^
$s.TargetPath = '%INSTALL_DIR%\uninstall.bat'; ^
$s.Save()"

echo.
echo =====================================
echo Installation Complete!
echo =====================================
echo.
echo ✔ Desktop shortcut created
echo ✔ Start Menu shortcut created
echo ✔ Uninstall option added
echo.

pause
endlocal
exit