try:
    while True:
        inp = input().split()
        n, m = int(inp[0]), int(inp[1])
        map = [[0 for i in range(m)] for i in range(n)]
        for i in range(n):
            inp = input().split()
            for ind, j in enumerate(inp):
                map[i][ind] = int(j)
        h = []
        for i in range(n):
            for j in range(m):
                d = map[i][j]
                total = 0
                for k in range(i-d, i+d+1):
                    if k<0 or k>=n:
                        continue
                    for l in range(j-d, j+d+1):
                        if l<0 or l>=m:
                            continue
                        dis = abs(k-i) + abs(l-j)
                        if dis<=d:
                            total += map[k][l]
                if total%10 == d:
                    h.append((i, j))
        print(len(h))
        print("\n".join(f"{i} {j}" for i, j in h))
except EOFError:
    pass