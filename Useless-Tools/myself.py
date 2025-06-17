# myself bbs downloader
# by AvianJay

# 注意: 此處全程使用土法煉鋼術，請不要看到變成腦弱
# 更新: 變成部分使用了

import os
import re
import sys
import json
import asyncio
import requests
import urllib.parse
import subprocess
import websockets
from tqdm import tqdm
from bs4 import BeautifulSoup

def legalize_filename(filename):
    # 文件名合法化
    legal_filename = re.sub(r'\|+', '｜', filename)  # 处理 | , 转全型｜
    legal_filename = re.sub(r'\?+', '？', legal_filename)  # 处理 ? , 转中文 ？
    legal_filename = re.sub(r'\*+', '＊', legal_filename)  # 处理 * , 转全型＊
    legal_filename = re.sub(r'<+', '＜', legal_filename)  # 处理 < , 转全型＜
    legal_filename = re.sub(r'>+', '＞', legal_filename)  # 处理 < , 转全型＞
    legal_filename = re.sub(r'\"+', '＂', legal_filename)  # 处理 " , 转全型＂
    legal_filename = re.sub(r':+', '：', legal_filename)  # 处理 : , 转中文：
    legal_filename = re.sub(r'\\', '＼', legal_filename)  # 处理 \ , 转全型＼
    legal_filename = re.sub(r'/', '／', legal_filename)  # 处理 / , 转全型／
    return legal_filename

def get_info(url):
    if "myself-bbs.com" not in url:
        print("[ERROR] Unsupported url.")
        return False
    res = requests.get(url)
    soup = BeautifulSoup(res.text, "html.parser")
    result = {"id": 0, "name": "", "episodes": []}
    for u in soup.find_all("a"):
        if u.get("href"):
            if u.get("href") in url:
                result["name"] = u.text.split("【")[0]
    ml = soup.find("ul", class_="main_list")
    # counter = 1
    for l in ml.find_all("a"):
        if "javascript" in l.get("href"):
            continue
        if not "v.myself-bbs.com" in l.get("data-href", ""):
            # only "站內"
            continue
        #print(l.get("data-href"))
        fs = l.get("data-href").split("/")
        result["id"] = fs[-2]
        #result["episode"].append(int(fs[-1]))
        try:
            result["episodes"].append({'episode': int(fs[-1]), 'method': 'vid', 'data': int(fs[-1])})
        except:
            ep = l.parent.parent.parent.find("a").text
            match = re.search(r"第\s*(\d+)\s*話", ep)
            if match:
                episode = int(match.group(1))
            result["episodes"].append({'episode': episode, 'method': 'id', 'data': fs[-1].strip("\r")})
        # counter += 1
    return result

def download(url, file, program="ffmpeg"):
    cmd = [program, '-protocol_whitelist', 'file,http,https,tcp,tls', '-i', url, '-acodec', 'copy', '-http_persistent', '0', '-vcodec', 'copy', file]
    tr = 0
    while tr <= 5:
        try:
            subprocess.run(cmd)
            return True
        except Exception as e:
            print("[WARN] Failed to download. Tried", tr, "times.", e)
            tr+=1
            continue
    print("[ERROR] Giving up.")
    return False

async def websocket_request(tid="", vid="", id=""):
    tr = 0
    while tr <= 5:
        try:
            headers = {
                'Upgrade': 'websocket',
                'Origin': 'https://v.myself-bbs.com',
                'Cache-Control': 'no-cache',
                'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                'Pragma': 'no-cache',
                'Connection': 'Upgrade',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            }
            uri = "wss://v.myself-bbs.com/ws"
            data = {"tid": tid, "vid": vid, "id": id}
            async with websockets.connect(uri, additional_headers=headers) as ws:
                await ws.send(json.dumps(data))
                response = await asyncio.wait_for(ws.recv(), timeout=10)
                await ws.close()
            try:
                response = json.loads(response)
                return response
            except json.JSONDecodeError:
                print("[ERROR] Failed to decode JSON response.")
                return False
        except Exception as e:
            print("[WARN] Failed to request video URL. Tried", tr, "times.", str(e))
            tr+=1
            continue
    print("[ERROR] Giving up.")
    return False
    
def request_url(episode, tid=""):
    if episode["method"] == "vid":
        data = asyncio.run(websocket_request(tid=tid, vid=episode["data"]))
    elif episode["method"] == "id":
        data = asyncio.run(websocket_request(id=episode["data"])) 
    else:
        print("[ERROR] Unknown method.")
        return False
    if not data:
        print("[ERROR] Failed to get data.")
        return False
    return "https:" + data["video"]

def get_base_url(id: str):
    b = "https://vpx05.myself-bbs.com/vpx/"
    print("[INFO] Trying to find video base url...")
    r = requests.get(b + id + "/001/")
    if r.status_code == 403:
        return b + id + "/xxx/"
    for i in range(1, 100):
        print("[INFO] Trying v" + str(i).zfill(2))
        r = requests.get(b + id + "/001_v" + str(i).zfill(2))
        if r.status_code == 403:
            print("[INFO] Found v" + str(i).zfill(2))
            return f"{b + id}/xxx_v{str(i).zfill(2)}/"
    print("[ERROR] Failed to find video base url.")
    return False

def download_all(url):
    print("[INFO] Getting info...")
    info = get_info(url)
    if not info:
        return False
    print("[INFO] Downloading:", info["name"])
    print("[INFO] Episodes:", len(info["episodes"]))
    # baseurl = get_base_url(info["id"])
    # if not baseurl:
    #     return False
    safename = legalize_filename(info["name"])
    try:
        os.mkdir(safename)
    except:
        pass
    for e in info["episodes"]:
        print("[INFO] Downloading episode", e["episode"])
        filepath = os.path.abspath(os.path.join(safename, f"{safename} [{str(e['episode']).zfill(2)}].mp4"))
        # url = baseurl.replace("xxx", str(e).zfill(3)) + "720p.m3u8"
        url = request_url(e, info["id"])
        if not url:
            print("[ERROR] Failed to get url. Skipping episode.")
            continue
        download(url, filepath)
    print("[INFO] Done.")
    return safename

def generate_agpp(path):
    os.chdir(path)
    response = requests.get("https://raw.githubusercontent.com/AvianJay/useless-script/refs/heads/main/Useless-Tools/agpp_custom_generator.py")
    if response.status_code == 200:
        script = response.text.replace('if len(sys.argv)>1:\n    exp["source"] = sys.argv[1]\nelse:\n    exp["source"] = input("Source?: ")', 'exp["source"] = "Myself"')
        exec(script)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        r = download_all(sys.argv[1])
        if not r:
            print("[ERROR] Failed.")
            sys.exit(1)
        if len(sys.argv) > 2:
            if sys.argv[2] == "true":
                generate_agpp(os.path.abspath(r))
    else:
        print("[ERROR] Invalid arguments.")
        print("[INFO] Usage:", sys.argv[0], "[myself-bbs.com thread URL] [gen aGP?]")
