# myself bbs downloader
# by AvianJay

# 注意: 此處全程使用土法煉鋼術，請不要看到變成腦弱

import os
import re
import sys
import json
import requests
import subprocess
import urllib.parse
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
    for l in ml.find_all("a"):
        if "javascript" in l.get("href"):
            continue
        fs = l.get("data-href").split("/")
        result["id"] = fs[-2]
        result["episodes"].append(int(fs[-1]))
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
            continue
    print("[ERROR] Giving up.")
    return False

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
    baseurl = get_base_url(info["id"])
    if not baseurl:
        return False
    safename = legalize_filename(info["name"])
    try:
        os.mkdir(safename)
    except:
        pass
    for e in info["episodes"]:
        print("[INFO] Downloading episode", e)
        filepath = os.path.abspath(os.path.join(safename, f"{safename} [{str(e).zfill(2)}].mp4"))
        url = baseurl.replace("xxx", str(e).zfill(3)) + "720p.m3u8"
        download(url, filepath)
    print("[INFO] Done.")
    return safename

def generate_agpp(path):
    os.chdir(path)
    script = """import os
import sys
import cv2
import json
import random

exp = {"videos": []}

exp["anime_name"] = os.path.basename(os.getcwd())
print("Anime name:", exp["anime_name"])
exp["source"] = "Myself"
exp["unique_sn"] = str(random.randint(0, 999999)).zfill(6)
print("unique sn:", exp["unique_sn"])

for _, __, files in os.walk("."):
    for file in files:
        vid = cv2.VideoCapture(file)
        resolution = int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT))
        episode_stage1 = file.split("[")[1].split("]")[0]
        # 據我所知的
        if "ova" in episode_stage1.lower():
            type = "OVA"
            episode_stage2 = episode_stage1.lower().replace("ova", "")
            if episode_stage2 == "":
                episode = 1
            else:
                episode = int(episode_stage2)
        elif "sp" in episode_stage1.lower():
            type = "SP"
            episode_stage2 = episode_stage1.lower().replace("sp", "")
            if episode_stage2 == "":
                episode = 1
            else:
                episode = int(episode_stage2)
        else:
            type = "normal"
            episode = int(episode_stage1)
        exp["videos"].append({"episode": episode, "resolution": resolution, "type": type, "filename": file})

json.dump(exp, open(".aniGamerPlus.json", "w"))
print("Done.")
"""
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
