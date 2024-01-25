@echo off 
title Auto Clicker Installer
echo Press any key to install.
pause > nul
cd %USERPROFILE%
%SYSTEMDRIVE%
echo Making dir...
md autoclicker
cd autoclicker
echo Downloading file...
curl https://nchc.dl.sourceforge.net/project/orphamielautoclicker/AutoClicker.exe --output autoclicker.exe
echo Creating Shortcut...
echo set WshShell = WScript.CreateObject("WScript.Shell") >> findDesktop.vbs
echo strDesktop = WshShell.SpecialFolders("Desktop") >> findDesktop.vbs
echo wscript.echo(strDesktop) >> findDesktop.vbs
cscript //Nologo findDesktop.vbs >> deskdir.tmp
set /p deskdir=<deskdir.tmp
echo Set oWS = WScript.CreateObject("WScript.Shell") > CreateShortcut.vbs
echo sLinkFile = "%deskdir%\AutoClicker.lnk" >> CreateShortcut.vbs
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> CreateShortcut.vbs
echo oLink.TargetPath = "%USERPROFILE%\autoclicker\autoclicker.exe" >> CreateShortcut.vbs
echo oLink.Save >> CreateShortcut.vbs
cscript //b CreateShortcut.vbs
del CreateShortcut.vbs
echo Creating Uninstall script...
echo @echo off >> uninstall.bat
echo title Auto Clicker Uninstaller >> uninstall.bat
echo echo Do You Want To Uninstall Auto Clicker? >> uninstall.bat
echo echo Press Any Key To Uninstall. >> uninstall.bat
echo pause >> uninstall.bat
echo cd %USERPROFILE% >> uninstall.bat
echo rmdir /S /Q %USERPROFILE%\autoclicker >> uninstall.bat
echo echo Uninstalled.Press Any Key To Exit. >> uninstall.bat
echo pause >> uninstall.bat
echo exit >> uninstall.bat
echo Installed.
echo Press Any Key To exit Installer.
pause > nul
exit