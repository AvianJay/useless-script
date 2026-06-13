import sys
input = sys.stdin.readline

n, m = map(int, input().split())
hl = list(map(int, input().split()))
th = {i + 1: hl[i] for i in range(n)}
t2tl = {}
for _ in range(m):
    tm = list(map(int, input().split()))
    t2tl[tm[0]] = t2tl.get(tm[0], []) + [{"f": tm[1], "l": tm[2]}]

s, t = map(int, input().split())

def go(ts, tt, ch):
    ch += th[ts]
    if ts == tt:
        return ch
    cg = t2tl.get(ts, [])
    if not cg:
        return -1
    sf = []
    for c in cg:
        if ch - c["l"] < 0:
            continue
        r = go(c["f"], tt, ch - c["l"])
        if r != -1:
            sf.append(r)
    sf.sort()
    if len(sf) == 0:
        return -1
    return sf[-1]

res = go(s, t, 0)
if res == -1:
    print("IMPOSSIBLE")
else:
    print(res)
