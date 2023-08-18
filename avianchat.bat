@echo off

:check
echo Checking...
IF EXIST "C:\Program Files\Google\Chrome\Application\chrome_proxy.exe" (
	goto :connect
    ) ELSE (
	echo You don't have Chrome installed, press any button to recheck.
	pause
	goto :check
    )
:connect
echo Connecting...
cd %temp%
curl https://avianjay.eu.org/text.txt --output test.txt > nul
IF EXIST test.txt (
	del test.txt
	"C:\Program Files\Google\Chrome\Application\chrome_proxy.exe" --app=http://avianjay.eu.org/chat.html
	exit
    ) ELSE (
	echo Can't Connect to Server.
	Press Any Key To Reconnect.
	pause
	goto :connect
    )
