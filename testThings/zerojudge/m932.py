try:
    while True:
        inp = input().split()
        m = int(inp[0])
        n = int(inp[1])
        k = int(inp[2])
        a = []
        for i in range(m):
            strings = input()
            to_add = []
            for j in strings:
                to_add.append(j)
            a.append(to_add)
        
        def goto(x, y, d):
            # 0: y-1; 1: x+1; 2: y+1, x+1; 3: y+1; 4: x-1; 5: x-1, y-1
            if d == 0:
                return (x, y - 1)
            elif d == 1:
                return (x + 1, y)
            elif d == 2:
                return (x + 1, y + 1)
            elif d == 3:
                return (x, y + 1)
            elif d == 4:
                return (x - 1, y)
            elif d == 5:
                return (x - 1, y - 1)
            
        steps = input().split()
        steps = [int(x) for x in steps]
        walked_strings = ""
        walked_types = 1
        x, y = 0, m - 1
        first_string = a[y][x]
        for i in steps:
            to_move = goto(x, y, i)
            if to_move[0] < 0 or to_move[0] >= n or to_move[1] < 0 or to_move[1] >= m:
                walked_strings += a[y][x]
                continue
            if a[to_move[1]][to_move[0]] not in walked_strings and a[to_move[1]][to_move[0]] != first_string:
                walked_types += 1
            walked_strings += a[to_move[1]][to_move[0]]
            x, y = to_move[0], to_move[1]
        print(walked_strings)
        print(walked_types)
except EOFError:
    pass