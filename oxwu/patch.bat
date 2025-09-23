@ECHO OFF
TITLE OXWU Patch Script
TASKKILL /F /IM OXWU.exe
DEL %LOCALAPPDATA%\OXWU\resources\app\app\main.js
COPY main.js %LOCALAPPDATA%\OXWU\resources\app\app\main.js
ECHO Patch Applied!