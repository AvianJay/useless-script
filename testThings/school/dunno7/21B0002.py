import sys


def main():
    data = sys.stdin.buffer.read().decode().split('\n')
    t = int(data[0])
    answers = []
    line = 1

    for _ in range(t):
        n, k = map(int, data[line].split())
        line += 1

        # 把 "0 1 0 0" 變成 "0100"，順便清掉 Windows 換行的 \r
        bed = data[line].replace(' ', '').replace('\r', '')
        line += 1
        while len(bed) < n:
            bed += data[line].replace(' ', '').replace('\r', '')
            line += 1

        # 頭尾各補一個 0，這樣邊界不用特別判斷
        bed = '0' + bed + '0'

        # 切成一段一段的連續空地，長度 L 的段可以種 (L-1)//2 朵
        can = 0
        for gap in bed.split('1'):
            if len(gap) > 1:
                can += (len(gap) - 1) // 2

        answers.append('true' if k <= can else 'false')

    sys.stdout.write('\n'.join(answers) + '\n')


main()
