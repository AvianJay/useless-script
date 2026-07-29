import sys
input = sys.stdin.readline

d = [100, 50, 10, 5, 1]
res = ""

money = int(input())

for i in d:
    res = res + f"{money // i} "
    money %= i

a = list(res.strip())
a.reverse()
print("".join(a))