# 讀取輸入
n = int(input())
characters = []

for _ in range(n):
    a, d = map(int, input().split())
    characters.append((a, d))

# 計算能力值 (攻擊力與防禦力的平均值)
characters_with_ability = [(a, d, (a + d) / 2) for a, d in characters]

# 按能力值排序，從大到小
characters_with_ability.sort(key=lambda x: x[2], reverse=True)

# 取得能力值第二大的角色
second_highest = characters_with_ability[1]

# 輸出攻擊力與防禦力
print(second_highest[0], second_highest[1])
