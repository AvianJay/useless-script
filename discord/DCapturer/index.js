const { app, BrowserWindow } = require("electron");
const express = require("express");
const fs = require("fs");
const path = require("path");

const configPath = path.join(__dirname, "config.json");
const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));

let win;

app.whenReady().then(async () => {
    win = new BrowserWindow({
        show: !config.hide,
        width: 1200,
        height: 800,
        webPreferences: {
            preload: __dirname + "/preload.js",
            offscreen: config.hide
        }
    });

    win.loadURL("https://discord.com/channels/@me");

    if (config.token) {
        await win.webContents.executeJavaScript(`
            (function login(token) {
                function _login(token) {
                    setInterval(() => {
                    document.body.appendChild(document.createElement("iframe")).contentWindow.localStorage.token = '"' + token + '"';
                    }, 50);
                    setTimeout(() => {
                    location.reload();
                    }, 2500);
                }
                _login("${config.token}");
            })("${config.token}");
        `);
    }

    // 起 Express Server
    const server = express();

    server.get("/screenshot/:guildId/:channelId/:messageId", async (req, res) => {
        const { guildId, channelId, messageId } = req.params;

        // 切頻道
        await win.webContents.executeJavaScript(`
            history.pushState({}, "", "/channels/${guildId}/${channelId}/${messageId}");
            window.dispatchEvent(new PopStateEvent("popstate"));
        `);

        // 等待訊息載入
        const rect = await win.webContents.executeJavaScript(`
            new Promise((resolve) => {
                const id = "chat-messages-${channelId}-${messageId}";
                const el = document.getElementById(id);
                if (el) {
                const r = el.getBoundingClientRect();
                resolve({ x: r.x, y: r.y, width: r.width, height: r.height });
                return;
                }

                const observer = new MutationObserver(() => {
                const el2 = document.getElementById(id);
                if (el2) {
                    const r2 = el2.getBoundingClientRect();
                    observer.disconnect();
                    setTimeout(() => {
                        resolve({ x: r2.x, y: r2.y, width: r2.width, height: r2.height });
                    }, 800);
                }
                });

                observer.observe(document.body, { childList: true, subtree: true });

                setTimeout(() => {
                observer.disconnect();
                resolve(null);
                }, 10000);
            });
        `);

        if (!rect) {
            res.status(404).send("Message not found");
            return;
        }

        const image = await win.webContents.capturePage(rect);
        res.contentType("image/png");
        res.send(image.toPNG());
    });

    server.listen(3000, () =>
        console.log("Server running on http://localhost:3000")
    );
});
