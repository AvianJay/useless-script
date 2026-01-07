s = [int(i) for i in input("輸入數字(以空格分隔): ").strip().split()]

for i in range(len(s)):
    for j in range(i+1, len(s)):
        print(f"i: {s[i]}, j: {s[j]}")
        if s[i] > s[j]:
            s[i], s[j] = s[j], s[i]

print(s)

import numpy as np

s = np.random.randint(1, 1001, size=100).tolist()

for i in range(len(s)):
    for j in range(i+1, len(s)):
        print(f"i: {s[i]}, j: {s[j]}")
        if s[i] > s[j]:
            s[i], s[j] = s[j], s[i]

print(s)