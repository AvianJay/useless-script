try:
    while True:
        inp = input().split()
        n = int(inp[0])
        m = int(inp[1])
        a = []
        for i in range(n):
            a.append([int(x) for x in input().split()])

        cx, cy, cv = 0, 0, 999999
        for i, iv in enumerate(a):
            for ji, jv in enumerate(iv):
                if jv < cv:
                    cv = jv
                    cx, cy = i, ji
        # print(cx, cy, cv)

        def get_four_sides(x, y):
            return [
                (x, y - 1, a[x][y - 1]) if y > 0 else None,
                (x - 1, y, a[x - 1][y]) if x > 0 else None,
                (x, y + 1, a[x][y + 1]) if y < m - 1 else None,
                (x + 1, y, a[x + 1][y]) if x < n - 1 else None,
            ]

        walked = []
        walked_total = cv
        while True:
            walked.append((cx, cy))
            four_sides = get_four_sides(cx, cy)
            lowest = (0, 0, 999999)
            for i in four_sides:
                if i is None:
                    continue
                fx, fy, fv = i
                if fv < lowest[2] and (fx, fy) not in walked:
                    lowest = (i[0], i[1], i[2])
            if lowest[2] == 999999:
                break
            cx, cy = lowest[0], lowest[1]
            walked_total += lowest[2]
        print(walked_total)
except EOFError:
    pass