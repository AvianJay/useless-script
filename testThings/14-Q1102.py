n, m = map(int, input().split())
maze = [[c for c in input().strip()] for _ in range(n)]
# @代表當前位置
# %代表迷宮出口
# 英文大寫字母代表傳送門，相同字母代表同種傳送門
# 數字0代表可走路徑，1代表牆壁
start = None
portals = {}
for i in range(n):
    for j in range(m):
        if maze[i][j] == '@':
            start = (i, j)
        elif maze[i][j].isupper():
            if maze[i][j] not in portals:
                portals[maze[i][j]] = []
            portals[maze[i][j]].append((i, j))
directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
visited = set()
queue = [(start[0], start[1], 0)]  # (row, col, steps)
visited.add(start)
found = False
while queue:
    x, y, steps = queue.pop(0)
    if maze[x][y] == '%':
        print(steps)
        found = True
        break
    for dx, dy in directions:
        nx, ny = x + dx, y + dy
        if 0 <= nx < n and 0 <= ny < m and (nx, ny) not in visited:
            if maze[nx][ny] == '0' or maze[nx][ny] == '%':
                visited.add((nx, ny))
                queue.append((nx, ny, steps + 1))
            elif maze[nx][ny].isupper():
                # Teleportation
                for px, py in portals[maze[nx][ny]]:
                    if (px, py) != (nx, ny) and (px, py) not in visited:
                        visited.add((px, py))
                        queue.append((px, py, steps + 1))
                visited.add((nx, ny))
                queue.append((nx, ny, steps + 1))
if not found:
    print(-1)
