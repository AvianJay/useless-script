import sys
input = sys.stdin.readline

n = int(input().strip())

poss = []
for _ in range(n):
    poss.append(list(map(int, input().strip().split())))
    
def measure(x1, y1, x2, y2):
    return (((x1-x2)**2)+((y1-y2)**2))**(1/2)

lest = 67676767676767676767.00

for pos in poss:
    for pos2 in poss:
        if pos == pos2:
            continue
        l = measure(pos[0], pos[1], pos2[0], pos2[1])
        lest = l if l < lest else lest

print(lest)