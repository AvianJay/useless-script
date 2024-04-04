import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
from base64 import b64decode
import json
import argparse
import sys
from time import sleep
from tqdm import tqdm
import random
def genj(id, ans, points):
    t = {
            'id': id,
            'content': [],
            'duration': 0,
            'contentNum': len(ans),
            'answerNum': len(ans),
            'correctContentNum': 0,
            'correctAnswerNum': 0,
            'incorrectContentNum': 0,
            'incorrectAnswerNum': 0,
            'userTypeCode': '單一選擇題',
            'pointsPerAnswer': points,
            'answerScore': None,
        }
    if len(ans)>1:
        for index, value in enumerate(ans):
            t['content'].append({
                    'id': id + '-' + str(index) ,
                    'answeringMethod': '單一選擇題',
                    'questionIndex': 0,
                    'answers': [
                        [
                            value,
                        ],
                    ],
                    'corrected': None,
                    'correctness': None,
                    'answerCorrected': None,
                    'answerNum': 1,
                    'correctAnswerNum': 0,
                    'incorrectAnswerNum': 0,
                    'mark': False,
                    'markDel': [],
                    'pointsPerContent': points,
                    'isManuallyGraded': False,
                    'answerScore': None,
                })
        t['userTypeCode'] = '題組'
    else:
        t['content'].append({
                    'id': '595454b4d1e9472faced140d3f97598b',
                    'answeringMethod': '單一選擇題',
                    'questionIndex': 0,
                    'answers': [
                        [
                            ans[0],
                        ],
                    ],
                    'corrected': None,
                    'correctness': None,
                    'answerCorrected': None,
                    'answerNum': 1,
                    'correctAnswerNum': 0,
                    'incorrectAnswerNum': 0,
                    'mark': False,
                    'markDel': [],
                    'pointsPerContent': points,
                    'isManuallyGraded': False,
                    'answerScore': None,
                })
    return t

def get(tid):
    headers = {
        'authority': 'hanlintest.ehanlin.com.tw',
        'accept': 'text/plain, application/json, text/json',
        'accept-language': 'zh-TW,zh;q=0.9',
        'origin': 'https://qt.hle.com.tw',
        'referer': 'https://qt.hle.com.tw/',
        'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'cross-site',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    }

    params = {
        'id': tid,
    }

    response = requests.get('https://hanlintest.ehanlin.com.tw/hanlintest-api/Examine/taskMetadata', params=params, headers=headers)
    return response.json()

def getcid(qcode):
    headers = {
        'authority': 'hanlintest.ehanlin.com.tw',
        'accept': '*/*',
        'accept-language': 'zh-TW,zh;q=0.9',
        'origin': 'https://qt.hle.com.tw',
        'referer': 'https://qt.hle.com.tw/',
        'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'cross-site',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    }

    params = {
        'activationId': qcode,
    }

    got_json = requests.get('https://hanlintest.ehanlin.com.tw/hanlintest-api/Examine/testItem', params=params,
                            headers=headers).json()
    tid = got_json["taskId"]
    cid = got_json["courseId"]
    print("任務名稱：", got_json['taskName'])
    print('得到任務ID:', tid, '課程ID:', cid)
    return {'c':cid,'t':tid}

def ansparser(ans):
    qs = []
    es = []
    print("共有", len(ans['items']), "題(不包含子題目)，開始獲取...")
    for q in tqdm(ans['items']):
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-TW,zh;q=0.9',
            'Connection': 'keep-alive',
            'Referer': 'https://qt.hle.com.tw/',
            'Sec-Fetch-Dest': 'iframe',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }

        params = {
            'v': '0.4.15',
            'bucket': 'itembank',
            'source': 'cf',
            't': '1705749370399',
        }

        response = requests.get(
            f"https://d1ocvtypb16jkr.cloudfront.net/v1/items/{q['itemId']}/assets/frameHtml/question-node.html",
            params=params,
            headers=headers,
        )
        soup = BeautifulSoup(response.text, features="lxml")
        qa = json.loads(unquote(b64decode(soup.find_all('script')[0].string.split('itemData = "')[1].split('"')[0]).decode()))
        try:
            add = {'id': q["itemId"], 'a': [qa['answer'][0][0]], 'p': q['pointsPerAnswer']}
        except:
            if qa['children']:
                anss = []
                for qc in qa['children']:
                    anss.append(qc['answer'][0][0])
                add = {'id': q["itemId"], 'a': anss, 'p': q['pointsPerAnswer']}
            else:
                es.append(qa)
        if not qs == None:
            qs.append(add)
    print("獲取到", len(qs), "個題目，其中有", len(qs)-len(ans['items']), "個子題目，共", len(es), "個錯誤")
    if len(es)>=1:
        print("有錯誤的題目！\n上傳ErrorQuestions.json到 https://github.com/AvianJay/useless-script/issues 讓開發者解決問題。")
        open("ErrorQuestions.json", 'w').write(json.dumps(es))
        print("成功儲存錯誤的題目到 ErrorQuestions.json")
    return qs

def genall(tid, eid):
    metadata = get(tid)
    ans = ansparser(metadata)
    a = {"examAnswerId":eid,"questionAnswers":[]}
    for i in ans:
        a["questionAnswers"].append(genj(i['id'], i['a'], i['p']))
    return a

def startexam(tid, cid, token=None, name=None, seatNo=None):
    if token:
        print("正在獲取帳號資訊...")
        headers = {
            'authority': 'hanlintest.ehanlin.com.tw',
            'accept': 'text/plain, application/json, text/json',
            'accept-language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en-XA;q=0.7,en;q=0.6,ar-XB;q=0.5,ar;q=0.4',
            'authorization': f'Bearer {token}',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'origin': 'https://qt.hle.com.tw',
            'pragma': 'no-cache',
            'referer': 'https://qt.hle.com.tw/',
            'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'cross-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        }

        sdatap = {
            'courseId': cid,
        }

        sdata = requests.post('https://hanlintest.ehanlin.com.tw/hanlintest-api/Student/token/get', headers=headers, json=sdatap).json()
        print("成功獲取帳號資訊。\n名稱:", sdata["name"], "\n座號:", sdata["seatNo"], "\nID:", sdata["id"])
    else:
        headers = {
            'authority': 'hanlintest.ehanlin.com.tw',
            'accept': 'text/plain, application/json, text/json',
            'accept-language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en-XA;q=0.7,en;q=0.6,ar-XB;q=0.5,ar;q=0.4',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'origin': 'https://qt.hle.com.tw',
            'pragma': 'no-cache',
            'referer': 'https://qt.hle.com.tw/',
            'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'cross-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        }
        sdata = {"name":name,"seatNo":seatNo, "id":None}

    json_data = {
        'taskId': tid,
        'userName': sdata["name"],
        'seatNo': str(sdata["seatNo"]),
        'studentId': sdata["id"],
    }

    print("正在發送開始作答請求...")

    response = requests.post(
        'https://hanlintest.ehanlin.com.tw/hanlintest-api/Examine/answer/createExamAnswer',
        headers=headers,
        json=json_data,
    ).json()
    print("成功。 ID為", response['id'])
    return response['id']

def sendans(data):
    headers = {
        'authority': 'hanlintest.ehanlin.com.tw',
        'accept': 'text/plain, application/json, text/json',
        'accept-language': 'zh-TW,zh;q=0.9',
        'origin': 'https://qt.hle.com.tw',
        'referer': 'https://qt.hle.com.tw/',
        'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'cross-site',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    }
    response = requests.post(
        'https://hanlintest.ehanlin.com.tw/hanlintest-api/Examine/answer/finishExamAnswer',
        headers=headers,
        json=data,
    )

if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--token", help="帳戶Token", dest="token", default=False)
    parser.add_argument("-n", "--name", help="名字", dest="name", default=False)
    parser.add_argument("-s", "--seat", help="座號", dest="seat", default=False)
    parser.add_argument("-i", "--id", help="題目的ID", dest="id")
    parser.add_argument("-r", "--random", help="隨機作答題數", dest="randomqs", default=False)
    parser.add_argument('-sk', '--skip-random-check', help="跳過確認隨機作答提醒", dest="skipr", action='store_true')
    parser.add_argument("-w", "--wait", help="等待秒數", dest="wait", default=False)
    args = parser.parse_args()
    if not args.token and not args.name and not args.seat:
        print("請指定Token或姓名與座號！")
        sys.exit(1)
    elif args.token and args.name:
        print("請不要同時指定Token和姓名與座號！")
        sys.exit(1)
    elif args.token and args.seat:
        print("請不要同時指定Token和姓名與座號！")
        sys.exit(1)
    elif args.name and not args.seat:
        print("請指定座號！")
        sys.exit(1)
    elif not args.name and args.seat:
        print("請指定姓名！")
        sys.exit(1)
    if args.randomqs:
        try:
            tmp = int(args.randomqs)
        except:
            print("指定的隨機作答題數不是一個有效的數字！")
            sys.exit(1)
    if args.wait:
        try:
            tmp = int(args.randomqs)
        except:
            print("指定的等待秒數不是一個有效的數字！")
            sys.exit(1)
    ctid = getcid(args.id)
    cid = ctid['c']
    tid = ctid['t']
    if args.token:
        eid = startexam(tid, cid, token=args.token)
    else:
        eid = startexam(tid, cid, name=args.name, seatNo=args.seat)
    print('開始生成所有正確答案...')
    ans = genall(tid, eid)
    oans = ans
    print('成功。')
    if args.randomqs:
        print('已指定隨機答案', args.randomqs, '個。正在生成...')
        replace_indices = random.sample(range(len(ans["questionAnswers"])), int(args.randomqs))
        b = []
        a = []
        for i in replace_indices:
            for j in ans["questionAnswers"][i]["content"]:
                b.append(j["answers"][0][0])

        # 遍歷資料
        for i, item in enumerate(ans["questionAnswers"]):
            if i in replace_indices:
                for content_item in item["content"]:
                    # 對答案進行替換
                    for answer in content_item["answers"]:
                        for j in range(len(answer)):
                            # 生成隨機數字並替換原答案
                            answer[j] = str(random.randint(1, 4))
                            a.append(answer[j])
        print("成功。\n本來的答案：", b, '\n現在的答案：', a)
        if not args.skipr:
            while True:
                ask = input("是否繼續或重新生成答案?(Y/n) ")
                if ask == "n":
                    ans = oans
                    replace_indices = random.sample(range(len(ans["questionAnswers"])), int(args.randomqs))
                    b = []
                    a = []
                    for i in replace_indices:
                        for j in ans["questionAnswers"][i]["content"]:
                            b.append(j["answers"][0][0])

                    # 遍歷資料
                    for i, item in enumerate(ans["questionAnswers"]):
                        if i in replace_indices:
                            for content_item in item["content"]:
                                # 對答案進行替換
                                for answer in content_item["answers"]:
                                    for j in range(len(answer)):
                                        # 生成隨機數字並替換原答案
                                        answer[j] = str(random.randint(1, 4))
                                        a.append(answer[j])
                    print("成功。\n本來的答案：", b, '\n現在的答案：', a)
                else:
                    break
    if args.wait:
        print('已指定等待送出時間。\n', args.wait, '秒。正在等待...')
        for i in tqdm(range(int(args.wait))):
            sleep(1)
    print("正在發送答案...")
    sendans(ans)
    print(f"成功。\n結果網址: https://qt.hle.com.tw/paper.html?id={eid}")

