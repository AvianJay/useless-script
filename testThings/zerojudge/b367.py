at = int(input())
for i in range(at):
    inp = input().split()
    n = int(inp[0])
    m = int(inp[1])
    o = []
    for j in range(n):
        a = list(map(int, input().split()))
        o.append(a)
    c = []
    for j in range(n):
        ta = []
        for k in range(m):
            ta.append(o[-1-j][-1-k])
        c.append(ta)
    
    if o == c:
        print("go forward")
    else:
        print("keep defending")
