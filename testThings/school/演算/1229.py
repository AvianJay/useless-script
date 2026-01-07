import numpy as np
import time

rs = time.perf_counter()
s = np.random.randint(1, 1001, size=100000).tolist()
re = time.perf_counter()
dt = re - rs

ss = time.perf_counter()
for i in range(len(s)):
    for j in range(i+1, len(s)):
        # print(f"i: {s[i]}, j: {s[j]}")
        if s[i] > s[j]:
            s[i], s[j] = s[j], s[i]
se = time.perf_counter()
sd = se - ss

print(s)
print("Done. Generate time:", round(dt, 2), "Sort time:", round(sd, 2))