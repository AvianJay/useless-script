import sys

n = int(next(sys.stdin).strip().split()[0])
for s in sys.stdin:
    a = s.strip().split()
    b = next(sys.stdin).strip().split()
    t = 0
    for v in a:
        if v in b:
            t += 1
    print(t)