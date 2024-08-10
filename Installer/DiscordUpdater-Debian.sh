if [ -e /tmp/Discord.deb ]
then
sudo rm /tmp/Discord.deb
fi
wget "https://discord.com/api/download?platform=linux&format=deb" -O /tmp/Discord.deb
sudo dpkg -i /tmp/Discord.deb
export returncode=$?
if [ $returncode == 0 ]
then
screen -L -dmS Discord /bin/bash -c discord
else
echo ERROR! Cannot install Discord!
echo Press any key to continue...
read
exit 1
fi
exit
