import sys
input = sys.stdin.readline

howmuch = int(input().strip())
j = []
for _ in range(howmuch):
    t, s = input().strip().split()
    j.append((t, s,))
j = sorted(j, key=lambda e: len(e[0]), reverse=True)
f = 0
do = input().strip()
while do:
    for i in j:
        if do.startswith(i[0]):
            f += int(i[1])
            do = do[len(i[0]):]
print(f)