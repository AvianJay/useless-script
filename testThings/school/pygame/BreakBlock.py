import pygame
import sys
from enum import Enum
import random
import math
import requests
import os
import json
import socket

app_version = "0.1.0"
server_url = "http://localhost:5000/"
ONLINE = False
god_mode = True if sys.argv[-1] == "god" else False
if not os.path.exists("user.json"):
    user = {"token": None, "name": None, 'high_score': 0}
    with open("user.json", "w") as f:
        json.dump(user, f)
else:
    with open("user.json", "r") as f:
        user = json.load(f)
def check_online():
    global ONLINE
    try:
        response = requests.get(server_url + "api/app_version")
        if response.status_code == 200:
            latest_version = response.json().get("app_version")
            if latest_version != app_version:
                print(f"New version available: {latest_version}. You have {app_version}.")
            else:
                print("You have the latest version.")
            # create user if no token
            if user["token"] is None:
                print("Creating user...")
                response = requests.get(server_url + "api/create_user", params={"name": "Player"})
                if response.status_code == 201:
                    data = response.json()
                    user["token"] = data.get("token")
                    # default name to computer name
                    user["name"] = socket.gethostname()
                    with open("user.json", "w") as f:
                        json.dump(user, f)
                    print("User created.")
                else:
                    print("Could not create user.")
                    raise Exception("Could not create user.")
            else:
                # verify token by fetching user data
                response = requests.get(server_url + "api/get_user", params={"token": user["token"]})
                if response.status_code == 200:
                    data = response.json()
                    user["name"] = data.get("name")
                else:
                    print("Invalid token.")
            ONLINE = True
        else:
            print("Could not check for updates.")
            ONLINE = False
    except Exception as e:
        print(f"Failed to connect server. Error: {e}")
        ONLINE = False
check_online()

pygame.init()
# 打磚塊
pygame.display.set_caption("打磚塊")
screen = pygame.display.set_mode((800, 600))
clock = pygame.time.Clock()
if getattr(sys, 'frozen', False):
    fontdir = sys._MEIPASS
else:
    fontdir = "."
fontpath = os.path.join(fontdir, "notobold.ttf")
if not os.path.exists(fontpath):
    fontpath = None
font = pygame.font.Font(fontpath, 36)
class Color(Enum):
    BLACK = (0, 0, 0)
    WHITE = (255, 255, 255)
    RED = (255, 0, 0)
    GREEN = (0, 255, 0)
    BLUE = (0, 0, 255)
    GRAY = (128, 128, 128)
def clear_screen():
    screen.fill(Color.BLACK.value)

class Block(pygame.sprite.Sprite):
    def __init__(self, x, y, unbreakable=False):
        pygame.sprite.Sprite.__init__(self)
        self.image = pygame.Surface((18, 18))
        self.image.fill(Color.BLUE.value if not unbreakable else Color.GRAY.value)
        self.rect = self.image.get_rect()
        self.rect.x = x
        self.rect.y = y
        self.unbreakable = unbreakable
    
class Paddle(pygame.sprite.Sprite):
    def __init__(self):
        pygame.sprite.Sprite.__init__(self)
        self.image = pygame.Surface((100, 10))
        self.image.fill(Color.GREEN.value)
        self.rect = self.image.get_rect()
        self.rect.x = 350
        self.rect.y = 550
    
    def update(self):
        # move paddle with mouse
        mouse_x = pygame.mouse.get_pos()[0]
        self.rect.x = mouse_x - 50

class Ball(pygame.sprite.Sprite):
    def __init__(self, x, y, balls_list):
        pygame.sprite.Sprite.__init__(self)
        # circle surface
        self.image = pygame.Surface((15, 15), pygame.SRCALPHA)
        pygame.draw.circle(self.image, Color.WHITE.value, (7, 7), 7)
        self.rect = self.image.get_rect()
        self.rect.x = x
        self.rect.y = y
        self.speed = [0, 2]
        self.balls_list = balls_list
    
    def update(self):
        # 移動球
        self.rect.x += self.speed[0]
        self.rect.y += self.speed[1]

        # limit speed to max 5
        if self.speed[0] > 5:
            self.speed[0] = 5
        if self.speed[0] < -5:
            self.speed[0] = -5
        if self.speed[1] > 5:
            self.speed[1] = 5
        if self.speed[1] < -5:
            self.speed[1] = -5

        # 邊界反彈
        if self.rect.left <= 0 or self.rect.right >= 800:
            self.speed[0] = -self.speed[0]
            if self.rect.left <= 0:
                self.rect.x = 0
            else:
                self.rect.x = 800 - self.rect.width
        if self.rect.top <= 0:
            self.speed[1] = -self.speed[1]
            if self.rect.top <= 0:
                self.rect.y = 0
            else:
                self.rect.y = 600 - self.rect.height
        # kill ball if bottom
        if self.rect.bottom >= 600:
            if god_mode:
                self.speed[1] = -self.speed[1]
                return
            else:
                self.kill()
                self.balls_list.remove(self)

class DoubleBallItem(pygame.sprite.Sprite):
    def __init__(self, x, y):
        pygame.sprite.Sprite.__init__(self)
        self.image = pygame.Surface((20, 20), pygame.SRCALPHA)
        pygame.draw.circle(self.image, Color.RED.value, (10, 10), 10)
        self.rect = self.image.get_rect()
        self.rect.x = x
        self.rect.y = y
        self.type = "on_ball" if random.random() < 0.1 else "on_paddle"
    
    def update(self):
        self.rect.y += 1
        if self.rect.top >= 600:
            self.kill()

class Button(pygame.sprite.Sprite):
    def __init__(self, x, y, width, height, text, callback):
        pygame.sprite.Sprite.__init__(self)
        self.image = pygame.Surface((width, height))
        self.image.fill(Color.BLACK.value)
        pygame.draw.rect(self.image, Color.GRAY.value, self.image.get_rect(), 5)
        font = pygame.font.Font(fontpath, 24)
        self.text = text
        self.text_surf = font.render(self.text, True, Color.WHITE.value)
        self.text_rect = self.text_surf.get_rect(center=(width // 2, height // 2))
        self.image.blit(self.text_surf, self.text_rect)
        self.rect = self.image.get_rect()
        self.x = x
        self.y = y
        self.rect.x = x
        self.rect.y = y
        self.callback = callback
    
    # def check_click(self):
    #     if self.rect.collidepoint(pygame.mouse.get_pos()):
    #         self.callback()
    
    def update(self):
        if self.rect.collidepoint(pygame.mouse.get_pos()):
            # to green
            pygame.draw.rect(self.image, Color.GREEN.value, self.image.get_rect(), 5)
            if pygame.mouse.get_pressed()[0]:
                self.callback()
        else:
            # to gray
            pygame.draw.rect(self.image, Color.BLACK.value, self.image.get_rect(), 5)

def start_game():
    unbreakable_positions = []
    # generate unbreakable blocks
    for i in range(2, 38):
        for j in range(2, 3):
            if random.random() < 0.55:
                unbreakable_positions.append((i, j))
                # if len(unbreakable_positions) >= 15:
                #     break

    all_sprites = pygame.sprite.Group()
    blocks = pygame.sprite.Group()
    unbreakable_blocks = pygame.sprite.Group()
    for i in range(2, 38):
        for j in range(2, 15):
            if (i, j) in unbreakable_positions:
                block = Block(i * 20 + 2, j * 20 + 2, unbreakable=True)
                unbreakable_blocks.add(block)
                continue
            block = Block(i * 20 + 2, j * 20 + 2)
            blocks.add(block)
    paddle = Paddle()
    balls = []
    ball = Ball(400, 400, balls_list=balls)
    all_sprites.add(ball, paddle, blocks, unbreakable_blocks)
    balls.append(ball)


    score = 0
    font = pygame.font.Font(fontpath, 15)
    score_text = font.render("Score: 0", True, (255, 255, 255))
    # screen.blit(score_text, (10, 10))
    show_score = True
    def increase_score():
        nonlocal score, score_text
        score += len(balls)
        score_text = font.render(f"Score: {score}", True, (255, 255, 255))
    restart = None
    while True:
        clock.tick(120)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
        # 如果沒有球了，遊戲結束
        # print("[DEBUG] Balls left:", len(balls))
        if not balls:
            restart = show_game_over_animation(score)
            break
        # if not blocks
        if not blocks:
            restart = show_win_animation(score)
            break

        # 更新精靈（會處理球與擋板位置與邊界反彈）
        all_sprites.update()

        # 球與擋板碰撞：只在球向下時反彈，並根據落點改變水平速度
        for ball in balls:
            if pygame.sprite.collide_rect(ball, paddle):
                if ball.speed[1] > 0:
                    # 依落點計算偏移量，讓球彈射角度改變
                    offset = (ball.rect.centerx - paddle.rect.centerx) / (paddle.rect.width / 3)
                    ball.speed[0] += offset * 2
                    ball.speed[1] = -abs(ball.speed[1])

        # WHYYYYY IT KILLS AND NOT BOUNCES BACK PROPERLY
        for ball in balls:
            for block in blocks:
                if pygame.sprite.collide_rect(ball, block):
                    dx = ball.rect.centerx - block.rect.centerx
                    dy = ball.rect.centery - block.rect.centery
                    # 若水平偏差較大，反轉水平速度，否則反轉垂直速度
                    if abs(dx) > abs(dy):
                        ball.speed[0] = -ball.speed[0]
                    else:
                        ball.speed[1] = -ball.speed[1]
                    block.kill()
                    increase_score()
                    # 20% 機率掉落雙球道具
                    if random.random() < 0.2:
                        item = DoubleBallItem(block.rect.x, block.rect.y)
                        all_sprites.add(item)
                    break
            for block in unbreakable_blocks:
                if pygame.sprite.collide_rect(ball, block):
                    dx = ball.rect.centerx - block.rect.centerx
                    dy = ball.rect.centery - block.rect.centery
                    # 若水平偏差較大，反轉水平速度，否則反轉垂直速度
                    if abs(dx) > abs(dy):
                        ball.speed[0] = -ball.speed[0]
                    else:
                        ball.speed[1] = -ball.speed[1]
                    break
        # hit_blocks = pygame.sprite.spritecollide(ball, blocks, True)
        # for block in hit_blocks:
        #     dx = ball.rect.centerx - block.rect.centerx
        #     dy = ball.rect.centery - block.rect.centery
        #     # 若水平偏差較大，反轉水平速度，否則反轉垂直速度
        #     if abs(dx) > abs(dy):
        #         ball.speed[0] = -ball.speed[0]
        #     else:
        #         ball.speed[1] = -ball.speed[1]

        # double ball item collection
        for item in [s for s in all_sprites if isinstance(s, DoubleBallItem)]:
            if pygame.sprite.collide_rect(item, paddle):
                # 產生第二顆球
                if item.type == "on_ball":
                    # iterate over a snapshot to avoid appending while iterating
                    existing_balls = list(balls)
                    new_balls = []
                    for b in existing_balls:
                        new_ball = Ball(b.rect.x, b.rect.y, balls_list=balls)
                        new_ball.speed = [-b.speed[0], -b.speed[1]]
                        all_sprites.add(new_ball)
                        new_balls.append(new_ball)
                    # extend after the iteration to avoid infinite loop
                    balls.extend(new_balls)
                else:
                    new_ball = Ball(paddle.rect.x, paddle.rect.y - 10, balls_list=balls)
                    # use an existing ball as reference for speed if any exist
                    ref = balls[0] if balls else None
                    if ref:
                        new_ball.speed = [-ref.speed[0], ref.speed[1]]
                    else:
                        # fallback speed
                        new_ball.speed = [0, -2]
                    all_sprites.add(new_ball)
                    balls.append(new_ball)
                item.kill()

        clear_screen()
        all_sprites.draw(screen)
        if show_score:
            screen.blit(score_text, (10, 10))
        pygame.display.update()


def title_screen():
    clear_screen()
    title_font = pygame.font.Font(fontpath, 72)
    title_surf = title_font.render("Break Block", True, Color.WHITE.value)
    title_rect = title_surf.get_rect(center=(400, 150))
    screen.blit(title_surf, title_rect)

    start_button = Button(350, 300, 100, 50, "Start", start_game)
    quit_button = Button(350, 400, 100, 50, "Quit", lambda: sys.exit())
    buttons = pygame.sprite.Group()
    buttons.add(start_button, quit_button)

    while True:
        clock.tick(60)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

        clear_screen()
        screen.blit(title_surf, title_rect)
        buttons.draw(screen)
        buttons.update()
        

        pygame.display.update()


def _show_end_animation(message, win, score, color, duration=2500):
    """Internal: show a short confetti + pulsing text animation (ms)."""
    start = pygame.time.get_ticks()
    particles = []
    now = pygame.time.get_ticks()
    # generate particles across top area
    for _ in range(80):
        x = random.randint(50, 750)
        y = random.randint(50, 250)
        vel = [random.uniform(-3.0, 3.0), random.uniform(-6.0, -1.0)]
        col = random.choice([Color.RED.value, Color.GREEN.value, Color.BLUE.value, Color.WHITE.value])
        life = random.randint(1200, 2200)
        particles.append({
            'pos': [x, y],
            'vel': vel,
            'col': col,
            'life': life,
            'born': now
        })
    
    escape = True
    restart = False
    
    def title_screen():
        global escape, restart
        escape = False
        restart = False
    
    def restart_game():
        global escape, restart
        escape = False
        restart = True
    
    restart_button = Button(350, 400, 100, 50, "Restart", restart_game)
    title_button = Button(350, 470, 100, 50, "Title", title_screen)
    buttons = pygame.sprite.Group()
    buttons.add(restart_button, title_button)
    
    

    while escape:
        dt = clock.tick(60)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            # allow skipping animation
            if e.type == pygame.KEYDOWN or e.type == pygame.MOUSEBUTTONDOWN:
                return

        t = pygame.time.get_ticks() - start
        clear_screen()

        # update & draw particles
        cur = pygame.time.get_ticks()
        for p in particles:
            age = cur - p['born']
            if age > p['life']:
                continue
            # simple gravity
            p['vel'][1] += 0.08
            p['pos'][0] += p['vel'][0]
            p['pos'][1] += p['vel'][1]
            alpha = max(0, 255 - int(age / p['life'] * 255))
            s = pygame.Surface((6, 6), pygame.SRCALPHA)
            s.fill((p['col'][0], p['col'][1], p['col'][2], alpha))
            screen.blit(s, (p['pos'][0], p['pos'][1]))

        # pulsing text
        scale = 1.0 + 0.25 * math.sin(t / 220.0)
        msg_font = pygame.font.Font(fontpath, max(10, int(64 * scale)))
        text_surf = msg_font.render(message, True, color)
        txt_rect = text_surf.get_rect(center=(400, 150))
        screen.blit(text_surf, txt_rect)

        # small subtitle with score
        sub = font.render(f"Score: {score}", True, (200, 200, 200))
        subr = sub.get_rect(center=(400, 200))
        screen.blit(sub, subr)
        # buttons
        buttons.draw(screen)
        buttons.update()

        pygame.display.update()

        # if t >= duration:
        #     break
    return restart



def show_game_over_animation(score):
    restart = _show_end_animation("Game Over!", False, score, Color.RED.value, duration=2500)
    return restart


def show_win_animation(score):
    restart = _show_end_animation("You Win!", True, score, Color.GREEN.value, duration=2500)
    return restart

if __name__ == "__main__":
    while True:
        title_screen()
        restart = start_game()
        if not restart:
            break
