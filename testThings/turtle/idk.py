import turtle

t = turtle.Turtle()
t.speed(0)
t.color("blue")
for i in range(180):
    t.forward(100)
    t.left(90)
    t.forward(100)
    t.left(90)
    t.forward(100)
    t.left(90)
    t.forward(100)
    t.left(90)
    t.left(2)

turtle.done()