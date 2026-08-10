# import sys
# input = sys.stdin.buffer.readline

n = int(input())

a = list(map(int, input().split()))
b = list(map(int, input().split()))

def closesta(num, l):
    c = 6766767667676767676767667667676676
    theresequal = False
    for i in l:
        if i > num:
            c = min(c, i)
            continue
        elif i == num:
            theresequal = True
    if c == 6766767667676767676767667667676676:
        if theresequal:
            c = num
        else:
            l.sort()
            c = l[0]
    return c

newt = []
for i in b:
    newt.append(closesta(i, a))
    a.remove(newt[-1])

# comp
w = 0
for i, v in enumerate(newt):
    if v > b[i]:
        w += 1
print(w)
