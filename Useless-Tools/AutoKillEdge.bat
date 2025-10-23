@ECHO OFF
title AutoKillEdge

:loop
tasklist /FI "IMAGENAME eq msedge.exe" 2>NUL | find /I /N "msedge.exe">NUL
if "%ERRORLEVEL%"=="0" (
    taskkill /F /IM msedge.exe
)
timeout /t 5 /nobreak > NUL
goto loop