import sys
input = sys.stdin.readline

board = []
for i in range(8):
    board.append(list(map(int, input().split())))

# find king (2)
gx, gy = 0, 0
for ii, row in enumerate(board):
    for ij, v in enumerate(row):
        if v == 2:
            gx, gy = ii, ij

# walk 8 directions from the king
dirs = [(-1, 0), (1, 0), (0, -1), (0, 1),
        (-1, -1), (-1, 1), (1, -1), (1, 1)]
for dx, dy in dirs:
    x, y = gx + dx, gy + dy
    while 0 <= x < 8 and 0 <= y < 8:
        if board[x][y] != 0:
            if board[x][y] == 1:
                print("True")
                sys.exit(0)
            break  # blocked by a non-queen piece
        x += dx
        y += dy

print("False")
