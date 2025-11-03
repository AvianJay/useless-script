import pygame
import sys
from enum import Enum
import random
import math
import requests
import os
import json
import socket
import threading

app_version = "0.1.0"
server_url = "https://breakblock.avianjay.sbs/"
ONLINE = False
# god_mode = True if sys.argv[-1] == "god" else False
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
                response = requests.get(server_url + "api/create_user", params={"name": socket.gethostname()})
                if response.status_code == 201:
                    data = response.json()
                    user["token"] = data.get("token")
                    # default name to computer name
                    user["name"] = data.get("name", "Unknown")
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
                    data = data.get("user", {})
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

def submit_score(score, win):
    if not ONLINE:
        print("Offline mode: score not submitted.")
        return False, "Offline mode"
    try:
        data = {
            # "name": user["name"],
            "score": score,
            "win": win,
            "app_version": app_version,
            "token": user["token"]
        }
        response = requests.post(server_url + "api/submit_score", json=data)
        if response.status_code == 201:
            print("Score submitted successfully.")
            return True, "Success"
        else:
            print("Failed to submit score.")
            return False, "Failed to submit score"
    except Exception as e:
        print(f"Error submitting score: {e}")
        return False, str(e)

def change_name(new_name):
    if not ONLINE:
        print("Offline mode: name not changed.")
        return False, "Offline mode"
    try:
        data = {
            "new_name": new_name,
            "token": user["token"]
        }
        response = requests.post(server_url + "api/edit_user_name", json=data)
        if response.status_code == 200:
            print("DEBUG: change_name response:", response.json())
            user["name"] = new_name
            with open("user.json", "w") as f:
                json.dump(user, f)
            print("Name changed successfully.")
            return True, "Success"
        else:
            print("Failed to change name.")
            return False, "Failed to change name"
    except Exception as e:
        print(f"Error changing name: {e}")
        return False, str(e)

pygame.init()
# 打磚塊
pygame.display.set_caption("打磚塊")
screen = pygame.display.set_mode((800, 600))
clock = pygame.time.Clock()
if getattr(sys, 'frozen', False):
    assetsdir = os.path.join(sys._MEIPASS, "assets")
else:
    assetsdir = "assets"
fontpath = os.path.join(assetsdir, "notobold.ttf")
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
        pygame.draw.rect(self.image, Color.GREEN.value, self.image.get_rect(), border_radius=15)
        self.rect = self.image.get_rect()
        self.rect.x = 350
        self.rect.y = 550
        self.can_move = True
    
    def update(self):
        # move paddle with mouse
        if self.can_move:
            mouse_x = pygame.mouse.get_pos()[0]
            self.rect.x = mouse_x - 50
    
    def crack(self):
        # add crack image on paddle
        self.can_move = False
        crack_image = pygame.image.load(os.path.join(assetsdir, "crack.png")).convert_alpha()
        crack_image = pygame.transform.scale(crack_image, (self.rect.width, self.rect.height))
        # use alpha blending to combine images
        surf = pygame.Surface((self.rect.width, self.rect.height), pygame.SRCALPHA)
        surf.blit(self.image, (0, 0))
        surf.blit(crack_image, (0, 0))
        self.image = surf

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
            # if god_mode:
            #     self.speed[1] = -self.speed[1]
            #     return
            # else:
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
        font = pygame.font.Font(fontpath, 24)
        self.text = text
        self.text_surf = font.render(self.text, True, Color.WHITE.value)

        # padding and ensure minimum size from provided width/height
        padding_x, padding_y = 20, 10
        w = max(width, self.text_surf.get_width() + padding_x)
        h = max(height, self.text_surf.get_height() + padding_y)

        # create surface sized to fit the text (or the provided minimum)
        self.image = pygame.Surface((w, h))
        self.image.fill(Color.BLACK.value)
        # draw initial border
        pygame.draw.rect(self.image, Color.GRAY.value, self.image.get_rect(), 5, 10)

        # center text on the surface
        text_rect = self.text_surf.get_rect(center=(w // 2, h // 2))
        self.image.blit(self.text_surf, text_rect)

        self.rect = self.image.get_rect()
        self.rect.x = x
        self.rect.y = y
        self.callback = callback
    
    # def check_click(self):
    #     if self.rect.collidepoint(pygame.mouse.get_pos()):
    #         self.callback()
    
    def update(self):
        if self.rect.collidepoint(pygame.mouse.get_pos()):
            # to green
            pygame.draw.rect(self.image, Color.GREEN.value, self.image.get_rect(), 5, 10)
            if pygame.mouse.get_pressed()[0]:
                self.callback()
        else:
            # to gray
            pygame.draw.rect(self.image, Color.GRAY.value, self.image.get_rect(), 5, 10)

# https://stackoverflow.com/a/46390412
COLOR_INACTIVE = pygame.Color('lightskyblue3')
COLOR_ACTIVE = pygame.Color('dodgerblue2')
FONT = pygame.font.Font(fontpath, 32)
class InputBox:
    def __init__(self, x, y, w, h, text='', callback=None):
        self.rect = pygame.Rect(x, y, w, h)
        self.color = COLOR_INACTIVE
        self.text = text
        self.txt_surface = FONT.render(text, True, self.color)
        self.active = False
        self.callback = callback

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            # If the user clicked on the input_box rect.
            if self.rect.collidepoint(event.pos):
                # Toggle the active variable.
                self.active = not self.active
            else:
                self.active = False
            # Change the current color of the input box.
            self.color = COLOR_ACTIVE if self.active else COLOR_INACTIVE
        if event.type == pygame.KEYDOWN:
            if self.active:
                if event.key == pygame.K_RETURN:
                    if self.callback:
                        self.callback(self.text)
                    self.text = ''
                elif event.key == pygame.K_BACKSPACE:
                    self.text = self.text[:-1]
                else:
                    self.text += event.unicode
                # Re-render the text.
                self.txt_surface = FONT.render(self.text, True, self.color)

    def update(self):
        # Resize the box if the text is too long.
        width = max(200, self.txt_surface.get_width()+10)
        self.rect.w = width

    def draw(self, screen):
        # Blit the text.
        screen.blit(self.txt_surface, (self.rect.x+5, self.rect.y+5))
        # Blit the rect.
        pygame.draw.rect(screen, self.color, self.rect, 2)

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
    score_text = font.render(f"分數: {score}", True, (255, 255, 255))
    # screen.blit(score_text, (10, 10))
    show_score = True
    def increase_score():
        nonlocal score, score_text
        score += len(balls)
        score_text = font.render(f"分數: {score}", True, (255, 255, 255))
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
            restart = show_game_over_animation(score, paddle)
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
                    # play pop sound
                    pop_sound = pygame.mixer.Sound(os.path.join(assetsdir, "pop.mp3"))
                    pop_sound.play()
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
    # print(restart)
    if restart:
        start_game()
    return


def title_screen():
    clear_screen()
    title_font = pygame.font.Font(fontpath, 72)
    title_surf = title_font.render("打磚塊", True, Color.WHITE.value)
    title_rect = title_surf.get_rect(center=(400, 150))
    screen.blit(title_surf, title_rect)
    high_score = user.get("high_score", 0)
    high_score_surf = font.render(f"最高分數: {high_score}", True, Color.WHITE.value)
    high_score_rect = high_score_surf.get_rect(center=(400, 220))
    screen.blit(high_score_surf, high_score_rect)
    
    def change_name_prompt():
        if not ONLINE:
            print("Cannot change name in offline mode.")
            return
        def handle_name_change(new_name):
            nonlocal success, msg
            success, msg = change_name(new_name)
        # center
        input_box = InputBox(300, 250, 200, 50, text=user["name"], callback=handle_name_change)
        done = False
        success, msg = None, None
        while not done:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                input_box.handle_event(event)
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_RETURN:
                        done = True
            input_box.update()
            clear_screen()
            screen.blit(title_surf, title_rect)
            input_box.draw(screen)
            pygame.display.update()
        if success is not None:
            print("Name change result:", msg)
            if success:
                print()

    start_button = Button(350, 300, 100, 50, "開始", start_game)
    quit_button = Button(350, 400, 100, 50, "退出", lambda: sys.exit())
    # print("DEBUG: user name is", user["name"])
    if not ONLINE:
        user["name"] = "已離線"
    change_name_button = Button(325, 500, 150, 50, user["name"], change_name_prompt)
    buttons = pygame.sprite.Group()
    buttons.add(start_button, quit_button, change_name_button)

    while True:
        clock.tick(60)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

        clear_screen()
        screen.blit(title_surf, title_rect)
        screen.blit(high_score_surf, high_score_rect)
        buttons.draw(screen)
        buttons.update()
        

        pygame.display.update()


def _show_end_animation(message, win, score, color, duration=2500):
    """Internal: show a short confetti + pulsing text animation (ms)."""
    start = pygame.time.get_ticks()
    particles = []
    now = pygame.time.get_ticks()
    # generate particles across top area
    if win:
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
        nonlocal escape, restart
        escape = False
        restart = False
    
    def restart_game():
        nonlocal escape, restart
        escape = False
        restart = True

    restart_button = Button(350, 400, 100, 50, "重新開始", restart_game)
    title_button = Button(350, 470, 100, 50, "標題", title_screen)
    buttons = pygame.sprite.Group()
    buttons.add(restart_button, title_button)
    
    success, msg = None, None
    def submit_score_thread():
        nonlocal success, msg
        success, msg = submit_score(score, win)
    threading.Thread(target=submit_score_thread).start()
    
    # update high score locally
    if score > user.get("high_score", 0):
        user["high_score"] = score
        with open("user.json", "w") as f:
            json.dump(user, f)

    while escape:
        dt = clock.tick(60)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

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

        if win:
            # pulsing text
            scale = 1.0 + 0.25 * math.sin(t / 220.0)
            msg_font = pygame.font.Font(fontpath, max(10, int(64 * scale)))
            text_surf = msg_font.render(message, True, color)
            txt_rect = text_surf.get_rect(center=(400, 150))
            screen.blit(text_surf, txt_rect)
        else:
            # text
            # transparent with time
            alpha = max(0, min(255, int((t / duration) * 255)))
            # print("DEBUG: alpha =", alpha)
            text_surf = font.render(message, True, color)
            txt_rect = text_surf.get_rect(center=(400, 150))
            screen.blit(text_surf, txt_rect)
            # overlay with alpha
            overlay = pygame.Surface((text_surf.get_width(), text_surf.get_height()), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 255 - alpha))
            screen.blit(overlay, txt_rect)

        # small subtitle with score
        sub = font.render(f"分數: {score}", True, (200, 200, 200))
        subr = sub.get_rect(center=(400, 200))
        screen.blit(sub, subr)
        # submission status
        status_font = pygame.font.Font(fontpath, 20)
        if success is not None:
            status_text = "分數已提交！" if success else f"分數提交失敗: {msg}"
            status_surf = status_font.render(status_text, True, (200, 200, 200))
            status_rect = status_surf.get_rect()
            status_rect.topleft = (10, 570)
            screen.blit(status_surf, status_rect)
        else:
            submitting_surf = status_font.render("提交分數中...", True, (200, 200, 200))
            submitting_rect = submitting_surf.get_rect()
            submitting_rect.topleft = (10, 570)
            screen.blit(submitting_surf, submitting_rect)
        # buttons
        buttons.draw(screen)
        buttons.update()

        pygame.display.update()

        # if t >= duration:
        #     break
    # print(restart)
    return restart



def show_game_over_animation(score, paddle):
    sound = pygame.mixer.Sound(os.path.join(assetsdir, "gameover-hurt.mp3"))
    sound.play()
    paddle.crack()
    t = pygame.time.get_ticks()
    while pygame.time.get_ticks() - t < 2000:
        clock.tick(60)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
        clear_screen()
        all_sprites = pygame.sprite.Group()
        all_sprites.add(paddle)
        all_sprites.draw(screen)
        pygame.display.update()
    paddle.kill()
    # generate fragments of paddle
    fragments = []
    for _ in range(30):
        x = paddle.rect.x + random.randint(0, paddle.rect.width)
        y = paddle.rect.y + random.randint(0, paddle.rect.height)
        vel = [random.uniform(-3.0, 3.0), random.uniform(-6.0, -1.0)]
        col = Color.GREEN.value
        life = random.randint(1000, 2000)
        fragments.append({
            'pos': [x, y],
            'vel': vel,
            'col': col,
            'life': life,
            'born': pygame.time.get_ticks()
        })
    sound = pygame.mixer.Sound(os.path.join(assetsdir, "gameover-disappear.mp3"))
    sound.play()
    t = pygame.time.get_ticks()
    while pygame.time.get_ticks() - t < 3000:
        dt = clock.tick(60)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

        clear_screen()

        # update & draw fragments
        cur = pygame.time.get_ticks()
        for p in fragments:
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

        pygame.display.update()
    music = pygame.mixer.Sound(os.path.join(assetsdir, "gameover-music.mp3"))
    music.play()
    restart = _show_end_animation("你輸了！", False, score, Color.RED.value, duration=2500)
    pygame.mixer.music.stop()
    return restart


def show_win_animation(score):
    restart = _show_end_animation("你贏了！", True, score, Color.GREEN.value, duration=2500)
    return restart

if __name__ == "__main__":
    while True:
        title_screen()
        restart = start_game()
        if not restart:
            break
