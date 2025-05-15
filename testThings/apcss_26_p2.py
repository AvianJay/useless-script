def move(x, y, px, py, d, x_min=1, y_min=1):
    if d==1:
        x+=1
        y+=1
    elif d==2:
        x+=1
        y-=1
    elif d==3:
        x-=1
        y-=1
    elif d==4:
        x-=1
        y+=1
    if x < x_min:
        x = x_min+1
        if d==3:
            d=2
        elif d==4:
            d=1
    elif x>px:
        x=px-1
        if d==1:
            d=4
        elif d==2:
            d=3
    if y<1:
        y = y_min+1
        if d==2:
            d=1
        elif d==3:
            d=4
    elif y>py:
        y=py-1
        if d==1:
            d=2
        elif d==4:
            d=3
    return x, y, d

def visualize(x, y, px, py):
    print(x, y, d)
    for i in range(py+2):
        for j in range(px+2):
            if i==0 or j==0 or j==px+1 or i==py+1:
                print("#", end="")
            elif j==x and i==y:
                print("O", end="")
            else:
                print(".", end="")
        print()

ixy = input().split()
px = int(ixy[0])
py = int(ixy[1])
d = int(input())
k = int(input())
x = px//2
y = py//2
while k>0:
    pd = d
    x, y, d = move(x, y, px, py, d, x_min=1, y_min=1)
    if pd!=d:
        k-=1
    #visualize(x, y, px, py)
print(x, y)