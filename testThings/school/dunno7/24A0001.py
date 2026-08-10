from collections import deque
import sys


def solve():
    input = sys.stdin.readline

    line = input().split()
    if not line:
        return
    m, n = int(line[0]), int(line[1])

    # 1. 直接讀成字串列表，不要把字元轉成 int (速度差好幾倍)
    maz = [input().strip() for _ in range(m)]

    # 2. BFS 找可達點
    reachable = set()
    queue = deque([(0, 0)])
    reachable.add((0, 0))

    directions = ((1, 0), (-1, 0), (0, 1), (0, -1))

    while queue:
        r, c = queue.popleft()
        for dr, dc in directions:
            nr, nc = r + dr, c + dc
            if 0 <= nr < m and 0 <= nc < n:
                if maz[nr][nc] == "1" and (nr, nc) not in reachable:
                    reachable.add((nr, nc))
                    queue.append((nr, nc))

    # 3. 檢查死路
    dead_ends = []
    for r, c in reachable:
        if r == 0 and c == 0:
            continue

        road_count = 0
        for dr, dc in directions:
            nr, nc = r + dr, c + dc
            if 0 <= nr < m and 0 <= nc < n:
                if maz[nr][nc] == "1":
                    road_count += 1

        if road_count == 1:
            dead_ends.append((r, c))

    dead_ends.sort()

    # 4. 批次字串輸出 (極速 I/O，解決 TLE 的關鍵)
    out = [str(len(dead_ends))]
    for r, c in dead_ends:
        out.append(f"{r} {c}")

    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    solve()