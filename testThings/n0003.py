import sys
m, n = map(int, sys.stdin.readline().split())
dm = [list(map(int, sys.stdin.readline().split())) for _ in range(m)]

x, y = 0, 0

def cand(x, y):
    cands = []
    for r in range(4):
        if r == 0:
            nx = x + 1
            ny = y
        elif r == 1:
            nx = x - 1
            ny = y
        elif r == 2:
            nx = x
            ny = y + 1
        else:
            nx = x
            ny = y - 1
        if 0 <= nx < m and 0 <= ny < n:
            if dm[nx][ny] == 1:
                cands.append((nx, ny))
    return cands

def go(x, y, lx, ly):
    cp = dm[x][y]
    cands = cand(x, y)
    if len(cands) == 1:
        return 1, [[x, y]]
    t = 0
    cp = []
    for c in cands:
        if c[0] == lx and c[1] == ly:
            continue
        r, gxy = go(c[0], c[1], x, y)
        t += r
        cp.extend(gxy)
    return t, cp
t, cp = go(x, y, -1, -1)
print(t)
print("\n".join([" ".join(map(str, c)) for c in cp]))

    