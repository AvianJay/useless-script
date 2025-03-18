@echo off
type %USERPROFILE%\.ssh\id_rsa.pub >NUL
if %ERRORLEVEL%==0 (goto copy) else (goto err)

:err
echo %USERPROFILE%\.ssh\id_rsa.pub is not exist.
echo Run "ssh-keygen" to Generate a key.
exit 1

:ssherr
echo Can't connect SSH Server.
echo Make sure ssh is installed and this is a ssh server.
exit 1

:copy
set host=%1
type %USERPROFILE%\.ssh\id_rsa.pub | ssh %host% "cat >> ~/.ssh/authorized_keys"
if %ERRORLEVEL%==0 else (goto ssherr)
echo Copied Key!
echo Run "ssh %host%" to login!
exit 0
