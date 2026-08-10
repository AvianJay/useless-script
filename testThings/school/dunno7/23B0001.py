import sys
input = sys.stdin.readline
n, m = map(int, input().split())

dna1 = input().strip()
dna2 = input().strip()

# 讓第二條 DNA 較短，以減少 DP 使用的記憶體
if len(dna1) < len(dna2):
    dna1, dna2 = dna2, dna1

dp = [0] * (len(dna2) + 1)

for base1 in dna1:
    previous_diagonal = 0
    for j, base2 in enumerate(dna2, 1):
        old_dp_j = dp[j]
        if base1 == base2:
            dp[j] = previous_diagonal + 1
        else:
            dp[j] = max(dp[j], dp[j - 1])
        previous_diagonal = old_dp_j

print(dp[-1])
        