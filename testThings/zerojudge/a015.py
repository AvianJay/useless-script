while True:
    try:
        a = input()
        if a == "0 0":
            break
        else:
            a = list(map(int, a.split()))
            n = a[0]
            m = a[1]
            b = []
            for i in range(n):
                b.append(list(map(int, input().split())))
            c = []
            for i in range(m):
                c.append([b[j][i] for j in range(n)])
            for i in c:
                print(" ".join(map(str, i)))
    except EOFError:
        break