@echo off
timeout /t 3 /nobreak
taskkill /f /im "地牛Wake Up!.exe"
"%USERPROFILE%\AppData\Local\OXWU\Program\地牛Wake Up!.exe" --hidden
