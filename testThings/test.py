import sys

input = sys.stdin.read
data = input().split()
n = int(data[0])  # 樓數
heights = list(map(int, data[1:]))  # 樓高列表

m = 0  # 最長滑翔路徑
l = 1  # 當前滑翔路徑長度

for i in range(1, n):
    if heights[i] <= heights[i - 1]:  # 如果當前樓高小於等於前一棟樓
        l += 1  # 滑翔路徑加長
    else:
        m = max(m, l)  # 更新最長滑翔路徑
        l = 1  # 重置滑翔路徑長度

m = max(m, l)  # 最後再更新一次最長滑翔路徑
print(m)