# -*- coding: utf-8 -*-
# Module: iQIYI-Downloader
# Created on: 07-17-2023
# Authors: CrymanChen
# Version: 1.3
# stolen from https://github.com/CrymanChen/iQIYI-Downloader
# forked by AvianJay
# added features:
#  changed program to ffmpeg
#  fixed "No m3u8 data."
#  add subtitle downloader
# todo: use selenium to get dash link

import requests
import json
import re
import subprocess
import os
import sys
import time
import colorama
from colorama import Fore, Style

colorama.init()
program = 'ffmpeg'

if len(sys.argv)>1:
    link = sys.argv[1]
else:
    link = input('\ndash link: ')

headers = {
    'Accept': 'application/json, text/javascript',
    'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ru;q=0.6',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'DNT': '1',
    'Origin': 'https://www.iqiyi.com',
    'Pragma': 'no-cache',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Not.A/Brand";v="8", "Chromium";v="114", "Google Chrome";v="114"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
}

start_time = time.time()
request = requests.get(url=link, headers=headers)
end_time = time.time()
response_time = (end_time - start_time) * 1000

m3u8DataJson = request.json()
m3u8DataRaw = None
for v in m3u8DataJson["data"]["program"]["video"]:
    if "m3u8" in v:
        m3u8DataRaw = v["m3u8"]
if not m3u8DataRaw:
    print('No m3u8 data.')
else:
    print("0: No subtitle")
    c = 1
    for v in m3u8DataJson["data"]["program"]["stl"]:
        print(f"{c}: {v['_name']}")
        c+=1
    sub_sel = int(input("Select a subtitle: "))
    if sub_sel:
        try:
            sub_url = m3u8DataJson["data"]["dstl"] + m3u8DataJson["data"]["program"]["stl"][sub_sel-1]["srt"]
        except:
            print("Wrong input.")
            exit(2)
    else:
        sub_url = False

status_code = request.status_code
if status_code == 200:
    status_code_color = Fore.GREEN + str(status_code) + Style.RESET_ALL
elif status_code == 404:
    status_code_color = Fore.GREY + str(status_code) + Style.RESET_ALL
else:
    status_code_color = Fore.RED + str(status_code) + Style.RESET_ALL

if 0 <= int(response_time) <= 200:
    response_time_color = Fore.GREEN + str(int(response_time)) + Style.RESET_ALL
elif 200 < int(response_time) <= 460:
    response_time_color = Fore.YELLOW + str(int(response_time)) + Style.RESET_ALL
else:
    response_time_color = Fore.RED + str(int(response_time)) + Style.RESET_ALL

if request.status_code == 200 and m3u8DataRaw != None:
    try:
        print(f'\nm3u8 fetched. Status code: {status_code_color}, Time: {response_time_color} ms')
        name = input('\nOutput name: ')
        with open(f'{name}.m3u8', "w") as file:
            file.write(m3u8DataRaw)
        command = [program, '-protocol_whitelist', 'file,http,https,tcp,tls', '-i', f'{name}.m3u8', '-acodec', 'copy', '-http_persistent', '0', '-vcodec', 'copy', f'{name}.mp4']
        subprocess.run(command)
        if sub_url:
            try:
                print("Trying to download subtitle...")
                open(f'{name}.srt', 'wb').write(requests.get(sub_url).content)
                print("done")
            except:
                print("download failed :(")
        choice = input('\nDelete m3u8 file? (Y/N): ')
        if choice == 'Y' or choice == 'y':
            try:
                os.remove(f'{name}.m3u8')
                print('File deleted.')
            except:
                print('Error.')
                exit(1)
        elif choice == 'N' or choice == 'n':
            exit(0)
        else:
            print('Wrong input.')
            exit(2)
    except:
        print('Error.')
        exit(1)
else:
    print(f'Status code: {request.status_code}. No m3u8 data.')
    exit(1)
