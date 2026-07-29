import sys
input = sys.stdin.readline

r = int(input().strip())

coins = [1, 2, 5]

dp = [0] * (r + 1)
dp[0] = 1

for total in range(1, r + 1):
    for coin in coins:
        if total >= coin:
            dp[total] += dp[total - coin]

print(dp[r])