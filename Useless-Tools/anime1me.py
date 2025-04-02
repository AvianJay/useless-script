# anime1.me downloader
# by AvianJay
# todo: support more page type
#        find other pages

import os
import re
import sys
import json
import requests
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

# https://stackoverflow.com/a/16696317
def download_file(url, session, name, filename):
    headers = {
        'authority': 'chima.v.anime1.me',
        'accept': '*/*',
        'accept-language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'referer': 'https://anime1.me/',
        'sec-ch-ua': '"Not A(Brand";v="8", "Chromium";v="132"',
        'sec-ch-ua-mobile': '?1',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'video',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36',
    }
    with session.get(url, stream=True, headers=headers) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('Content-Length', 0))
        with open(filename, 'wb') as f:
            with tqdm(total=total_size, unit='B', unit_scale=True, desc=name) as pbar:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
    return filename

def get_mp4_url(data, session):
    headers = {
        'authority': 'v.anime1.me',
        'accept': '*/*',
        'accept-language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://anime1.me',
        'referer': 'https://anime1.me/',
        'sec-ch-ua': '"Not A(Brand";v="8", "Chromium";v="132"',
        'sec-ch-ua-mobile': '?1',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36',
    }

    response = session.post('https://v.anime1.me/api', headers=headers, data=f"d={data}").json()
    return "https:" + response["s"][0]["src"]

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
exp["source"] = "anime1.me"
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

def main(url, gen_agpp):
    videos = []
    session = requests.Session()
    web = session.get(url)
    soup = BeautifulSoup(web.text, "html.parser")
    title = soup.find('h1').text
    print("Downloading: ", title)
    basedir = legalize_filename(title)
    if not os.path.exists(basedir):
        try:
            os.mkdir(basedir)
        except:
            pass

    articles = soup.find_all('article')
    for a in articles:
        name = a.find("h2").find("a").text
        da = a.find("video").get("data-apireq")
        videos.append({"name": name, "apireq": da})

    for v in videos:
        path = os.path.join(basedir, legalize_filename(v["name"]) + ".mp4")
        print("Requesting for mp4 url...")
        mp4_url = get_mp4_url(v["apireq"], session)
        print("Started downloading:", v["name"])
        download_file(mp4_url, session, v["name"], path)
        print("Done.")
    if gen_agpp:
        generate_agpp(os.path.abspath(basedir))

if __name__ == "__main__":
    if not len(sys.argv) == 2 or not len(sys.argv) == 3:
        print("Invalid arguments.")
        print("Usage:", sys.argv[0], "[anime1.me URL]")
        exit(1)
    elif not "https://anime1.me" in sys.argv[1]:
        print("Invalid URL.")
        print("Usage:", sys.argv[0], "[anime1.me URL]")
        exit(1)
    if len(sys.argv) == 3:
        if sys.argv[2] == "true":
            main(sys.argv[1], True)
    else:
        main(sys.argv[1])
