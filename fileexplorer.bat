@echo off
title File Explorer
set now="%SYSTEMDRIVE%\"
%SYSTEMDRIVE%

:exp
cls
echo You Are On %now%
cd %now%
dir
set /p ans="Type a Option:"
if %ans%==? (echo Help=? & echo Go to a folder=f & echo Change Disk=cd & echo Create A File=c & echo Delete a file=d & echo Create A Folder=cf & echo Delete A Folder=df & pause & goto exp)
if %ans%==f (goto gfold)
if %ans%==cd (goto cd)
if %ans%==c (goto c)
if %ans%==d (goto delf)
if %ans%==cf (goto crefold)
if %ans%==df (goto delfold)
echo Type ? to get commands.
goto exp

:gfold
set /p fold="Type A folder:"
set now=%now%\%fold%
goto exp

:cd
set /p disk="Type A Disk(EX:C:):"
%disk%
set now=%disk%\
goto exp

:c
set /p file="Type A File Name:"
echo Something... >> %file%
goto exp

:delf
set /p file="Type A File name to delete:"
del %file%
goto exp

:crefold
set /p fold="Type A folder Name:"
mkdir %fold%
goto exp

:delfold
set /p fold="Type A folder Name To Delete:"
rmdir /s %fold%
goto exp