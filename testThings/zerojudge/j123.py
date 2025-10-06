# 定義貨物形狀
shapes = {
    'A': [(0,0),(1,0),(2,0),(3,0)],
    'B': [(0,0),(0,1),(0,2),(0,3)],
    'C': [(0,0),(1,0),(0,1),(1,1)],
    'D': [(0,0),(1,0),(2,0),(2,1)],
    'E': [(0,1),(1,0),(1,1),(1,2)]
}

R, C, n = map(int, input().split())
warehouse = [[0]*C for _ in range(R)]
discard = 0

for _ in range(n):
    t, d = input().split()
    d = int(d)
    shape = shapes[t]
    placed = False
    # 嘗試每個可能的左上角位置
    for col in range(C):
        # 計算此形狀最右邊會不會超出
        max_col = max(x for x, y in shape)
        if col + max_col >= C:
            continue
        # 計算此形狀最上面會不會超出
        min_row = d
        max_row = d + max(y for x, y in shape)
        if max_row >= R:
            continue
        # 檢查是否可放
        can_place = True
        for dx, dy in shape:
            x = col + dx
            y = d + dy
            if warehouse[y][x]:
                can_place = False
                break
        if can_place:
            for dx, dy in shape:
                x = col + dx
                y = d + dy
                warehouse[y][x] = 1
            placed = True
            break
    if not placed:
        discard += 1

# 計算剩餘空格
empty = sum(row.count(0) for row in warehouse)
print(empty, discard)