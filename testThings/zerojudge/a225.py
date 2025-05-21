try:
    while True:
        input()
        inp = input().split()
        o = [[] for i in range(10)]
        for i in range(10):
            for j in inp:
                j = int(j)
                if j%10==i:
                    o[i].append(j)
        result = ""
        for i in o:
            if i:
                i.sort(reverse=True)
                i = [str(j) for j in i]
                result += " ".join(i) + " "
        print(result.strip())
except EOFError:
    pass