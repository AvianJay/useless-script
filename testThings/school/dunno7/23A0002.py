import sys
input = sys.stdin.readline

n = int(input().strip())

if n == 0:
    print(1)
else:
    # Counts for valid strings ending in 0, 1, and 2, respectively.
    end_in_0 = end_in_1 = end_in_2 = 1

    for _ in range(1, n):
        total = end_in_0 + end_in_1 + end_in_2
        end_in_0, end_in_1, end_in_2 = (
            total - end_in_0,
            total - end_in_1,
            total,
        )

    print(end_in_0 + end_in_1 + end_in_2)