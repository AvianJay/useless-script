@echo off
title Internet Checker
echo Press Any Key To Check Internet.

:a
pause > nul
echo Checking...
curl 1.1.1.1 --output netcheck.tmp > nul
if exist "netcheck.tmp" (echo You Connected internet. & del netcheck.tmp) else (echo You are not connected to the internet.)
echo Press Any Key To Check Internet Again.
goto a
