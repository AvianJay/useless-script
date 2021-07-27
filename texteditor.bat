@echo off
title Text Editor

:ask
set /p ans="Do You Want To Read or Create Text?(r/c)"
if %ans%==r (goto read)
if %ans%==c (goto create)
echo Please Type r or c.
goto ask

:read
set /p file="File Location:"
if exist %file% (goto edit) else (echo This is not file. & goto read)

:create
set /p file="File Location:"
goto edit

:edit
cls
type %file%
set /p text=""
if %text%==/cleartext\ (del %file% & goto edit)
if %text%==/home\ (goto ask)
if %text%==/exit\ (exit)
echo %text% >> %file%
goto edit