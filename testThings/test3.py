import sys

input = sys.stdin.read
data = input().split()
n = int(data[0])
w1 = int(data[1])
w2 = int(data[2])
h1 = int(data[3])
h2 = int(data[4])
w1 = w1*w1
w2 = w2*w2
maxone = 0
for i in range(n):
    d = int(data[5+i])
    l = 0
    if d<=w1*h1:
        l += d//w1
        h1 = h1 - l
    elif d>w1*h1 and d<=w2*h2:
        l += (h1+(d-w1*h1)//w2)
        h2 = h2 - (d-w1*h1)//w2
        h1 = 0
    else:
        l += (h1+h2)
        h2 = 0
        h1 = 0
    maxone = max(maxone, l)
print(maxone)