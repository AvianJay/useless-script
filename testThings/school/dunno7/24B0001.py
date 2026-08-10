import sys
from array import array
from collections import deque

input = sys.stdin.buffer.readline

n, m = map(int, input().split())
width = m + 2
grid = bytearray(b"1") * ((n + 2) * width)
portals = [array("i") for _ in range(26)]
start = -1

# get orange pos and portal pos

for row_index in range(1, n + 1):
    row = input().strip()
    offset = row_index * width + 1
    grid[offset:offset + m] = row

    for column_index, tile in enumerate(row):
        position = offset + column_index
        if tile == ord("@"):
            start = position
        elif ord("A") <= tile <= ord("Z"):
            portals[tile - ord("A")].append(position)

distance = array("i", [-1]) * len(grid)
distance[start] = 0
queue = deque([start])
up = [False] * 26

# gaygaygay

while queue:
    position = queue.popleft()
    if grid[position] == ord("%"):
        print(distance[position])
        break

    next_distance = distance[position] + 1
    for next_position in (
        position - width,
        position + width,
        position - 1,
        position + 1,
    ):
        tile = grid[next_position]
        if tile == ord("1"):
            continue

        if ord("A") <= tile <= ord("Z"):
            portal_index = tile - ord("A")
            if up[portal_index]:
                continue

            up[portal_index] = True
            for destination in portals[portal_index]:
                if distance[destination] == -1:
                    distance[destination] = next_distance
                    queue.append(destination)
        elif distance[next_position] == -1:
            distance[next_position] = next_distance
            queue.append(next_position)
