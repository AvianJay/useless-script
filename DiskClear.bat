@echo off
del dl.dps
del clear.dps
cd /d "%~dp0" && ( if exist "%temp%\getadmin.vbs" del "%temp%\getadmin.vbs" ) && fsutil dirty query %systemdrive% 1>nul 2>nul || (  echo Set UAC = CreateObject^("Shell.Application"^) : UAC.ShellExecute "cmd.exe", "/k cd ""%~sdp0"" && %~s0 %params%", "", "runas", 1 >> "%temp%\getadmin.vbs" && "%temp%\getadmin.vbs" && exit /B )
title Warning!!!
cls
color 74
color 47

:warnask
set /p warn="Do You Want Continue?This Tool Can Clear Your Disk!(y/n)"
if %warn%==y (color 07 & goto dcstart)
if %warn%==n (exit)
echo Please Type y or n.
goto warnask

:dcstart
cls
cd %tmp%
title Disk Clear
echo list disk > dl.dps
echo ------Disk------
type diskpart /s dl.dps
echo ----------------
del dl.dps
set /p disk="Select A Disk:(Number)"
echo Making command...
echo sel dis %disk% > %tmp%/clear.dps
echo clean >> %tmp%/clear.dps
echo create part pri >> %tmp%/clear.dps
echo assign >> %tmp%/clear.dps
echo Running Command...
diskpart /s %tmp%/clear.dps
del clear.dps
echo OK!Press Any Key To Exit.
pause > nul