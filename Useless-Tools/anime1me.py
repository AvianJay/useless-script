# anime1.me downloader
# by AvianJay
# todo: support more page type
#        support ipp video(p2p, hls)
#        server mode so it can auto update
#        Done: find other pages

import os
import re
import sys
import json
import requests
import subprocess
#import curl_cffi as requests
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
            with tqdm(total=total_size, unit='B', unit_scale=True) as pbar:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
    return filename

def download_hls(url, file, program="ffmpeg"):
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
    #print(data)
    response = session.post('https://v.anime1.me/api', headers=headers, data=f"d={data}")
    return "https:" + response.json()["s"][0]["src"]

def download_anime(data, path, session):
    if not data["data"]:
        return False
    try:
        if data["method"] == "apireq":
            mp4_url = get_mp4_url(data["data"], session)
            download_file(mp4_url, session, data["name"], path)
            return True
        elif data["method"] == "p2p":
            download_hls(data["data"], path)
            return True
    except Exception as e:
        print("ERROR:", e)
        return False

def get_info(url, session):
    videos = []
    if "/page/" in url:
        url = url.split("/page/")[0]
    web = session.get(url)
    soup = BeautifulSoup(web.text, "html.parser")
    title = soup.find('h1', class_='page-title').text

    while True:
        articles = soup.find_all('article')
        for a in articles:
            name = a.find("h2").find("a").text
            # a.find("button").get("data-src")
            if a.find("video"):
                method = "apireq"
                data = a.find("video").get("data-apireq") if a.find("video") else None
            elif a.find("button"):
                method = "p2p"
                url = a.find("button").get("data-src")
                res = session.get(url)
                psoup = BeautifulSoup(res.text, "html.parser")
                data = psoup.find("source").get("src") if psoup.find("source") else None
            videos.append({"name": name, "method": method, "data": data})

        prevbtn = soup.find("div", class_="nav-previous")
        if prevbtn:
            print("Found another page.")
            web = session.get(prevbtn.find("a")["href"])
            soup = BeautifulSoup(web.text, "html.parser")
        else:
            break
    return title, videos

def get_anime_list(check_page_if_serializing=False, check_page_if_dot=False):
    alist = requests.get("https://d1zquzjgwo9yb.cloudfront.net/").json()
    ealist = []
    for a in alist:
        if "href=" in a[1]:
            url = a[1].split("href=\"")[1].split("\">")[0]
            name = a[1].split("\">")[0].split("</a>")[0]
        else:
            url = "https://anime1.me/?cat=" + str(a[0])
            name = a[1]

        episodes = []
        if "連載中" in a[2]:
            if check_page_if_serializing:
                t, vs = get_info(url, requests.session())
                for v in vs:
                    episodes.append(v["name"].split("[")[-1].split("]")[0])
            else:
                for i in range(1, int(a[2].split("(")[-1].split(")")[0].split()[0])+1):
                    episodes.append(str(i).zfill(2))
        elif "-" == a[2]:
            t, vs = get_info(url, requests.session())
            for v in vs:
                episodes.append(v["name"].split("[")[-1].split("]")[0])
        else:
            epsf = a[2].split("+")
            for e in epsf:
                if "ova" in e.lower():
                    if "ova" == e.lower() or "ova extra" == e.lower():
                        episodes.append(e)
                    else:
                        if "ova extra" in e.lower():
                            epss = e.lower().strip("ova extra").split("-")
                        else:
                            epss = e.lower().strip("ova").split("-")
                        if len(epss) == 1:
                            episodes.append(e)
                        else:
                            for i in range(int(epss[0]), int(epss[1])+1):
                                episodes.append(str(i).zfill(2))
                elif "sp" in e.lower():
                    if "sp" == e.lower():
                        episodes.append(e)
                    else:
                        epss = e.lower().strip("sp").split("-")
                        if len(epss) == 1:
                            episodes.append(e)
                        else:
                            for i in range(int(epss[0]), int(epss[1])+1):
                                episodes.append(str(i).zfill(2))
                elif "oad" in e.lower():
                    if "oad" == e.lower():
                        episodes.append(e)
                    else:
                        epss = e.lower().strip("oad").split("-")
                        if len(epss) == 1:
                            episodes.append(e)
                        else:
                            for i in range(int(epss[0]), int(epss[1])+1):
                                episodes.append(str(i).zfill(2))
                elif "ona" in e.lower():
                    if "ona" == e.lower() or "ona extra" == e.lower():
                        episodes.append(e)
                    else:
                        epss = e.lower().strip("ona").split("-")
                        if len(epss) == 1:
                            episodes.append(e)
                        else:
                            for i in range(int(epss[0]), int(epss[1])+1):
                                episodes.append(str(i).zfill(2))
                elif "extra" in e.lower():
                    if "extra" == e.lower():
                        episodes.append(e)
                    else:
                        epss = e.lower().strip("extra").split("-")
                        if len(epss) == 1:
                            episodes.append(e)
                        else:
                            for i in range(int(epss[0]), int(epss[1])+1):
                                episodes.append(str(i).zfill(2))
                elif e == "劇場版":
                    episodes.append(e)
                elif e == "特別編":
                    episodes.append(e)
                else:
                    epss = e.split("-")
                    if len(epss) == 1:
                        episodes.append(e)
                    else:
                        if "." in epss[1]:
                            if check_page_if_dot:
                                t, vs = get_info(url, requests.session())
                                episode = []
                                for v in vs:
                                    episodes.append(v["name"].split("[")[-1].split("]")[0])
                                break
                            else:
                                epss[1] = abs(float(epss[1]))
                                episodes.append(epss[1])
                        for i in range(int(epss[0]), int(epss[1])+1):
                            episodes.append(str(i).zfill(2))

        anime = {
            "name": name,
            "url": url,
            "episodes": episodes,
            "year": a[3],
            "season": a[4],
            "source": a[5]
        }
        ealist.append(anime)
    return ealist

def generate_agpp(path):
    os.chdir(path)
    response = requests.get("https://raw.githubusercontent.com/AvianJay/useless-script/refs/heads/main/Useless-Tools/agpp_custom_generator.py")
    if response.status_code == 200:
        script = response.text.replace('if len(sys.argv)>1:\n    exp["source"] = sys.argv[1]\nelse:\n    exp["source"] = input("Source?: ")', 'exp["source"] = "Anime1.me"')
        exec(script)

def main(url, gen_agpp):
    session = requests.Session()
    print("Getting info...")
    title, videos = get_info(url, session)
    print("Downloading: ", title)
    basedir = legalize_filename(title)
    if not os.path.exists(basedir):
        try:
            os.mkdir(basedir)
        except:
            pass
    print("Episodes:", len(videos))
    for v in videos:
        #print(v)
        if not v["data"]:
            print(v["name"], ": Unknown method. Skipping.")
            continue
        path = os.path.join(basedir, legalize_filename(v["name"]) + ".mp4")
        print("Trying to download with method", v["method"])
        if download_anime(v, path, session):
            print("Done.")
        else:
            print("Failed to download", v["name"])
    if gen_agpp:
        generate_agpp(os.path.abspath(basedir))

if __name__ == "__main__":
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Invalid arguments.")
        print("Usage:", sys.argv[0], "[anime1.me URL] [gen aGP?]")
        exit(1)
    elif not "https://anime1.me" in sys.argv[1]:
        print("Invalid URL.")
        print("Usage:", sys.argv[0], "[anime1.me URL] [gen aGP?]")
        exit(1)
    if len(sys.argv) == 3:
        if sys.argv[2] == "true":
            main(sys.argv[1], True)
    else:
        main(sys.argv[1], False)
