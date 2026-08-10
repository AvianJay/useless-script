import sys

input = sys.stdin.buffer.readline

n = int(input().strip())

station_index = {}
parent = []
size = []
degree = []


def add_station(station):
    if station not in station_index:
        index = len(parent)
        station_index[station] = index
        parent.append(index)
        size.append(1)
        degree.append(0)
    return station_index[station]


def find(node):
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def union(a, b):
    root_a = find(a)
    root_b = find(b)
    if root_a == root_b:
        return

    if size[root_a] < size[root_b]:
        root_a, root_b = root_b, root_a
    parent[root_b] = root_a
    size[root_a] += size[root_b]


while True:
    a, b = map(int, input().strip().split())
    if a == -1 and b == -1:
        break

    u = add_station(a)
    v = add_station(b)
    degree[u] += 1
    degree[v] += 1
    union(u, v)

leaves_by_component = {}
for node in range(len(parent)):
    if degree[node] == 1:
        root = find(node)
        leaves_by_component[root] = leaves_by_component.get(root, 0) + 1

answer = sum((leaves + 1) // 2 for leaves in leaves_by_component.values())
print(answer)

