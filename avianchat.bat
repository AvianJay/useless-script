@echo off

:check
echo Checking...
IF EXIST "C:\Program Files (x86)\Google\Chrome\Application\chrome_proxy.exe" (
	goto :connect
    ) ELSE (
	echo You don't have Chrome installed, press any button to recheck.
	pause
	goto :check
    )
:connect
echo Connecting...
cd %temp%
curl http://www.ajmcserver.ml/text.txt --output test.txt > nul
IF EXIST test.txt (
	del test.txt
	"C:\Program Files (x86)\Google\Chrome\Application\chrome_proxy.exe" --app=http://www.ajmcserver.ml/chat.html
	exit
    ) ELSE (
	echo Can't Connect to Server.
	Press Any Key To Reconnect.
	pause
	goto :connect
    )
