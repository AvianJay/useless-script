@echo off 
title MultiMC Installer
echo Press any key to install.
pause > nul
cd %USERPROFILE%
%SYSTEMDRIVE%
echo Downloading file...
curl https://files.multimc.org/downloads/mmc-develop-win32.zip --output mmc.zip
curl https://raw.githubusercontent.com/AvianJay/Morecord/main/7z.exe -o 7z.exe
curl https://raw.githubusercontent.com/AvianJay/Morecord/main/7z.dll -o 7z.dll
echo Unzipping Files...
7z x -aoa mmc.zip
del mmc.zip
del 7z.exe
del 7z.dll
echo Creating Shortcut...
md %APPDATA%\Microsoft\Windows\Start Menu\Programs\MultiMC\
echo set WshShell = WScript.CreateObject("WScript.Shell") >> findDesktop.vbs
echo strDesktop = WshShell.SpecialFolders("Desktop") >> findDesktop.vbs
echo wscript.echo(strDesktop) >> findDesktop.vbs
cscript //Nologo findDesktop.vbs >> deskdir.tmp
set /p deskdir=<deskdir.tmp
echo Set oWS = WScript.CreateObject("WScript.Shell") > CreateShortcut.vbs
echo sLinkFile = "%deskdir%\MultiMC.lnk" >> CreateShortcut.vbs
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> CreateShortcut.vbs
echo oLink.TargetPath = "%USERPROFILE%\MultiMC\MultiMC.exe" >> CreateShortcut.vbs
echo oLink.Save >> CreateShortcut.vbs
echo Set oWS = WScript.CreateObject("WScript.Shell") > CreateShortcutSM.vbs
echo sLinkFile = "%APPDATA%\Microsoft\Windows\Start Menu\Programs\MultiMC\MultiMC.lnk" >> CreateShortcutSM.vbs
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> CreateShortcutSM.vbs
echo oLink.TargetPath = "%USERPROFILE%\MultiMC\MultiMC.exe" >> CreateShortcutSM.vbs
echo oLink.Save >> CreateShortcut.vbs
cscript //b CreateShortcut.vbs
cscript //b CreateShortcutSM.vbs
del CreateShortcut.vbs
del CreateShortcutSM.vbs
echo Creating Uninstall script...
cd %USERPROFILE%\MultiMC\
echo @echo off >> uninstall.bat
echo title Auto Clicker Uninstaller >> uninstall.bat
echo echo Do You Want To Uninstall MultiMC? >> uninstall.bat
echo echo Press Any Key To Uninstall. >> uninstall.bat
echo pause >> uninstall.bat
echo Removing Link... >> uninstall.bat
echo del %deskdir%\MultiMC.lnk >> uninstall.bat
echo rmdir /S /Q %APPDATA%\Microsoft\Windows\Start Menu\Programs\MultiMC\ >> uninstall.bat
echo cd %USERPROFILE% >> uninstall.bat
echo rmdir /S /Q %USERPROFILE%\MultiMC >> uninstall.bat
echo echo Uninstalled.Press Any Key To Exit. >> uninstall.bat
echo pause >> uninstall.bat
echo exit >> uninstall.bat
echo Installed.
echo Press Any Key To exit Installer.
pause > nul
exit