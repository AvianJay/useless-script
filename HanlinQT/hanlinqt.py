import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
from base64 import b64decode
import json
import argparse
import sys

def ansparser(ans, filename):
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
            if qa['answer'][0][0]=="1":
                ans = "A"
            elif qa['answer'][0][0]=="2":
                ans = "B"
            elif qa['answer'][0][0]=="3":
                ans = "C"
            elif qa['answer'][0][0]=="4":
                ans = "D"
            else:
                ans = qa['answer'][0][0]
            add = {'q': qa['examquestion'], 'a': ans}
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
    if filename.lower().endswith(".json"):
        open(filename, 'w').write(json.dumps(qs))
        return f"成功儲存題目到 {filename}"
    else:
        open(filename + ".json", 'w').write(json.dumps(qs))
        return f"成功儲存題目到 {filename + '.json'}"

def get(tid, fn):
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
    return ansparser(response.json(), fn)

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
    template = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HanlinQT</title>
    <style>
        .keyword {
            color: blue;
            text-decoration: underline;
            cursor: pointer;
        }
    </style>
</head>
<body>

<script>
    var data = ['QAJSON'];

    for (var i = 0; i < data.length; i++) {
        var question = data[i].q;
        var answer = data[i].a;

        var questionContainer = document.createElement('div');
        questionContainer.innerHTML = question;

        var keywords = questionContainer.getElementsByClassName('keyword');
        for (var j = 0; j < keywords.length; j++) {
            keywords[j].addEventListener('click', function (event) {
                alert('你點擊了關鍵字: ' + event.target.dataset.keyword);
            });
        }

        var answerContainer = document.createElement('div');
        answerContainer.innerHTML = '<p>答案：' + answer + '</p>';

        document.body.appendChild(questionContainer);
        document.body.appendChild(answerContainer);
    }
</script>

</body>
</html>
    '''
    replaced = template.replace("['QAJSON']", str(questions))
    open(savename, 'w', encoding="utf-8").write(replaced)

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--id", help="題目的ID", dest="id", default=False)
    parser.add_argument("-s", "--savename", help="保存的json檔案名稱", dest="savename", default="HanlinQT.json")
    parser.add_argument("-j", "--json", help="taskMetadata的JSON路徑", dest="json_path", default=False)
    parser.add_argument("-g", "--genhtml", help="生成HTML的檔案名稱", dest="genhtml", default=False)
    args = parser.parse_args()

    if args.id and not args.json_path:
        print(get(getcid(args.id), args.savename))
    elif args.json_path and not args.id:
        print(ansparser(json.load(open(args.json_path, 'r', encoding="utf-8")), args.savename))
    else:
        print("錯誤！請提供題目ID或taskMetadata的JSON路徑(只能選1個)！")
        sys.exit(1)

    if args.genhtml:
        if args.genhtml.lower().endswith('.html'):
            savename = args.genhtml
        else:
            savename = args.genhtml + '.html'
        if args.savename.lower().endswith(".json"):
            q = open(args.savename, 'r', encoding="utf-8").read()
        else:
            q = open(args.savename + '.json', 'r', encoding="utf-8").read()
        create(q, savename)
        print("成功創建HTML到", savename)

