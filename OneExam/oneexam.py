import requests

headers = {
    'authority': 'asia-northeast1-oneexam-release.cloudfunctions.net',
    'accept': '*/*',
    'accept-language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en-XA;q=0.7,en;q=0.6,zh-CN;q=0.5',
    'authorization': 'undefined',
    'markauthorization': 'null',
    'origin': 'https://oneexam.oneclass.com.tw',
    'referer': 'https://oneexam.oneclass.com.tw/',
    'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'cross-site',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
}
def get(quizcode):
    got_json = requests.get(f'https://asia-northeast1-oneexam-release.cloudfunctions.net/api/quiz/112-1/{quizcode}', headers=headers).json()
    if got_json["status"] == "success":
        quizid = got_json['content']["paperId"]
        ans_url = f'https://oneexam.oneclass.com.tw/paper/preview/{quizid}'
    else:
        ans_url = f"無法獲取，錯誤訊息：{got_json['content']}"
    return ans_url


if __name__=="__main__":
    import sys
    if len(sys.argv)>1:
        print(get(sys.argv[1]))
    else:
        print("Usage:", sys.argv[0], '[QUIZID]')
