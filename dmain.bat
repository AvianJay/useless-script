@echo off
timeout /t 3 /nobreak
taskkill /f /im "�a��Wake Up!.exe"
"%USERPROFILE%\AppData\Local\OXWU\Program\�a��Wake Up!.exe" --hidden
