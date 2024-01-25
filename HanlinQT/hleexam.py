import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
from base64 import b64decode
import json

def ansparser(ans):
    qs = []
    es = []
    for q in ans['items']:
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
            add = {'q': qa['examquestion'], 'a': qa['answer'][0][0]}
        except:
            if qa['children']:
                add = {'q': qa['examquestion'], 'a': "此為題組題"}
                for qc in qa['children']:
                    qs.append({'q': qc['examquestion'], 'a': qc['answer'][0][0]})
            else:
                add = {'q': qa['examquestion'], 'a': "無法獲取答案"}
                es.append(qa)
        if not qs == None:
            qs.append(add)
    print("獲取到", len(qs), "個題目，共", len(es), "個錯誤")
    open('hqa.json', 'w').write(json.dumps(qs))
    open('her.json', 'w').write(json.dumps(es))
    return "成功儲存題目到 hqa.json"

def get(tid):
    headers = {
        'authority': 'hanlintest.ehanlin.com.tw',
        'accept': 'text/plain, application/json, text/json',
        'accept-language': 'zh-TW,zh;q=0.9',
        'authorization': 'Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6Ijg1NzgwNWYxZGQ3ZmE5YTZiNTI3ZjQ0ZWNmZmJkNDhjIiwidHlwIjoiYXQrand0In0.eyJuYmYiOjE3MDU3MTExODksImV4cCI6MTczNzg1MTk4OSwiaXNzIjoiaHR0cHM6Ly9pZC5obGUuY29tLnR3IiwiY2xpZW50X2lkIjoianMiLCJzdWIiOiJkY2M5NzBhZS1iMGE2LTQ0M2EtOWRiOC1mOWIxZGFmZjk2MzIiLCJhdXRoX3RpbWUiOjE3MDU3MTExODgsImlkcCI6Im1vZSIsImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL2VtYWlsYWRkcmVzcyI6InN0MTEwMzE1QG50ZXMudGMuZWR1LnR3QGhhbmxpbiIsIkFzcE5ldC5JZGVudGl0eS5TZWN1cml0eVN0YW1wIjoiVkRONFVMWVpIUlhMVlJGS0lTRklNVE01RFBJVE5DQzciLCJuYW1lIjoiNWYzMzhhZWMtYjdlNS00MDYwLTkzZmQtNDdiOWYwNGI1MzE0IiwiZW1haWwiOiJzdDExMDMxNUBudGVzLnRjLmVkdS50d0BoYW5saW4iLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwicHJlZmVycmVkX3VzZXJuYW1lIjoi5b6Q6bW_5YKRIiwidXNlcl9kb21haW4iOiJlZHUiLCJyb2xlIjoi5a2455SfIiwiZWR1QWNjb3VudCI6Im50ZXMxMDYwMzAzMTUiLCJlZHVJZCI6IjVmMzM4YWVjLWI3ZTUtNDA2MC05M2ZkLTQ3YjlmMDRiNTMxNCIsInNjaG9vbFN5c3RlbSI6IuWci-awkeWwj-WtuCIsImlzaWRlbnRpZmllZCI6ZmFsc2UsImxvY2siOmZhbHNlLCJ2ZXIiOjMsInNjb3BlIjpbIm9wZW5pZCIsInByb2ZpbGUiXSwiYW1yIjpbImV4dGVybmFsIl19.mh8FldJ42GxYSnlJhzLovZjC8cApfuMatj1Y5sDZ9QJtiJmHaW-CW80pgCXQXSxvuUno--rS6-l30QhuiRH7YDIh8E1H14zT3IzSFLsbrjckTptIWZGqZCrcRllDcYRom3Gs5xY2TdGWlI46lntcfvbGquZcyPrx1wTIN2uxTqRdXOrD8AtS1UmVbHPypaisJmFDlu5rCq09yoecIG91GZ9uCiWMUycFEX-c7Zdwp3YwhZoFUSVflI1J2H5Pu0X1xXCYRl0lMz5R75U6_VPJv8mCGTKTfjKMXp8UOHXHl-xJoSat3eB2DsQ2ioXOE4ii_QBkM5pec4VMpV1S3hJnEw',
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
    return ansparser(response.json())

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
    print('得到任務ID:', tid)
    return tid

def create(questions, savename):
    template = open('hleqa.html', 'r', encoding="utf-8").read()
    replaced = template.replace("['QAJSON']", str(questions))
    open(savename, 'w', encoding="utf-8").write(replaced)

if __name__=='__main__':
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "file" and len(sys.argv)>2:
            print(ansparser(json.load(open(sys.argv[2]))))
        else:
            print(get(getcid(sys.argv[1])))
        if sys.argv[-2] == "genhtml":
            if sys.argv[-1].lower().endswith('.html'):
                savename = sys.argv[-1]
            else:
                savename = sys.argv[-1] + '.html'
            q = open("hqa.json", 'r', encoding="utf-8").read()
            create(q, savename)
            print("成功創建HTML到", sys.argv[-1])
    else:
        print("用法:", sys.argv[0], '[試卷ID |或| file 題目.json]', '[genhtml <檔案.html>](可選)')
