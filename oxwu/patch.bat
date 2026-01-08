@ECHO OFF
TITLE OXWU Patch Script
TASKKILL /F /IM OXWU.exe
SET "APPDIR=%LOCALAPPDATA%\OXWU\resources\app"

REM Some installs have an extra "app" folder layer: resources\app\app\main.js
SET "MAINDIR=%APPDIR%"
IF EXIST "%APPDIR%\app\main.js" (
	SET "MAINDIR=%APPDIR%\app"
)

DEL "%MAINDIR%\main.js"
COPY main.js "%MAINDIR%\main.js"

REM Optional: copy node_modules (e.g. socket.io) so require() works.
REM OXWU expects node_modules at resources\app\node_modules (not inside the inner app folder).
IF EXIST node_modules (
	ECHO Copying node_modules into OXWU resources\app\node_modules...
	ROBOCOPY node_modules "%APPDIR%\node_modules" /E /R:2 /W:1 /NFL /NDL
)
ECHO Patch Applied!