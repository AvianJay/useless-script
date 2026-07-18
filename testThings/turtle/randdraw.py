import turtle
import random

t = turtle.Turtle()
t.speed(0)
while True:
    t.forward(random.randint(50, 150))
    t.left(random.randint(0, 360))
    # limit the turtle to stay within the screen
    if abs(t.xcor()) > 300 or abs(t.ycor()) > 300:
        # set the turtle back to the center of the screen
        t.goto(0, 0)
    print(f"x: {t.xcor()}, y: {t.ycor()}")
turtle.done()