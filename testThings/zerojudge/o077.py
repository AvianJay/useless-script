try:
    while True:
        inp = input().split()
        h = int(inp[0])
        w = int(inp[1])
        n = int(inp[2])
        map = [[0 for i in range(w)] for i in range(h)]
        for i in range(n):
            inp = input().split()
            r, c, t, x = int(inp[0]), int(inp[1]), int(inp[2]), int(inp[3])
            for j in range(r-t, r+t+1):
                if j<0 or j>=h:
                    continue
                for k in range(c-t, c+t+1):
                    if k<0 or k>=w:
                        continue
                    dis = abs(j-r) + abs(k-c)
                    if dis<=t:
                        map[j][k] += x

        s = "\n".join(" ".join(str(map[i][j]) for j in range(w)) for i in range(h))
        print(s)
except EOFError:
    pass