import sys

input = sys.stdin.buffer.readline

n, m = map(int, input().split())
parent = list(range(n + 1))
size = [1] * (n + 1)


def find(student):
    while parent[student] != student:
        parent[student] = parent[parent[student]]
        student = parent[student]
    return student


circle_count = n

for _ in range(m):
    student_a, student_b = map(int, input().split())
    root_a = find(student_a)
    root_b = find(student_b)

    if root_a == root_b:
        continue

    if size[root_a] < size[root_b]:
        root_a, root_b = root_b, root_a

    parent[root_b] = root_a
    size[root_a] += size[root_b]
    circle_count -= 1

print(circle_count)
    