# hanime1.me downloader
# Author: AvianJay

import os
import re
import sys
import json
import cloudscraper
from bs4 import BeautifulSoup
from tqdm import tqdm
import requests
import argparse

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
    with session.get(url, stream=True) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('Content-Length', 0))
        with open(filename, 'wb') as f:
            with tqdm(total=total_size, unit='B', unit_scale=True) as pbar:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
    return filename

def get_mp4_url(id, session):
    response = session.get(f'https://hanime1.me/download?v={id}')
    soup = BeautifulSoup(response.text, 'html.parser')
    # a tag class exoclick-popunder juicyads-popunder
    a_tag = soup.find('a', class_='exoclick-popunder juicyads-popunder')
    if a_tag:
        return a_tag['data-url']
    return None

def get_info(url, session):
    response = session.get(url)
    # video-playlist-wrapper
    soup = BeautifulSoup(response.text, 'html.parser')
    playlist_div = soup.find('div', id='video-playlist-wrapper')
    title = playlist_div.find('h4').text.strip()
    episode_urls = []
    scroll_div = playlist_div.find('div', id='playlist-scroll')
    for div in scroll_div.find_all('div', recursive=False):
        a_tag = div.find('a')
        if a_tag:
            episode_urls.append(a_tag['href'])
    episode_urls.reverse()  # 反轉順序，從第一集開始下載
    episodes = [{"episode": idx + 1, "url": ep_url} for idx, ep_url in enumerate(episode_urls)]
    return {
        "title": title,
        "episodes": episodes
    }
    
def generate_agpp(path):
    os.chdir(path)
    response = requests.get("https://raw.githubusercontent.com/AvianJay/useless-script/refs/heads/main/Useless-Tools/agpp_custom_generator.py")
    if response.status_code == 200:
        script = response.text.replace('if len(sys.argv)>1:\n    exp["source"] = sys.argv[1]\nelse:\n    exp["source"] = input("Source?: ")', 'exp["source"] = "Hanime1.me"')
        exec(script)

def main():
    parser = argparse.ArgumentParser(description="Hanime1.me Downloader")
    parser.add_argument("url", help="影片頁面URL")
    parser.add_argument("-a", "--agpp", action="store_true", help="生成AGPP檔案")
    args = parser.parse_args()

    url = args.url
    session = cloudscraper.create_scraper()
    info = get_info(url, session)
    title = legalize_filename(info['title'])
    if not os.path.exists(title):
        os.makedirs(title)
    print(f"開始下載動畫: {title}")
    print(f"總集數: {len(info['episodes'])}")
    for episode in info['episodes']:
        ep_num = episode['episode']
        ep_url = episode['url']
        print(f"正在處理第 {ep_num} 集...")
        # 從 ep_url 中提取 id 參數
        match = re.search(r'v=([a-zA-Z0-9]+)', ep_url)
        if not match:
            print(f"無法從 URL 中提取 ID: {ep_url}")
            continue
        video_id = match.group(1)
        mp4_url = get_mp4_url(video_id, session)
        if not mp4_url:
            print(f"無法取得第 {ep_num} 集的下載連結")
            continue
        filename = f"{title} [{ep_num}].mp4"
        filepath = os.path.join(title, filename)
        print(f"下載中: {filename}")
        download_file(mp4_url, session, title, filepath)
        print(f"第 {ep_num} 集下載完成!")
    print("所有集數下載完成!")
    if args.agpp:
        print("正在生成 AGPP 檔案...")
        generate_agpp(os.path.join(os.getcwd(), title))
        print("AGPP 檔案生成完成!")

if __name__ == "__main__":
    main()