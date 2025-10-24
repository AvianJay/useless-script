n = int(input())
at = {}
for i in range(n):
    data = input().split()
    at[data[0]] = int(data[1])
m = input()
total = 0
while m:
    highest = "", 0
    for k, v in at.items():
        if m.startswith(k):
            if v > highest[1]:
                highest = k, v
    total += highest[1]
    m = m[len(highest[0]):]
print(total)