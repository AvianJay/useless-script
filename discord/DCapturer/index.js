const { app, BrowserWindow } = require("electron");
const express = require("express");

let win;

app.whenReady().then(() => {
    win = new BrowserWindow({
        width: 1200,
        height: 800,
        webPreferences: {
            preload: __dirname + "/preload.js"
        }
    });

    win.loadURL("https://discord.com/channels/@me");

    // 起 Express Server
    const server = express();

    server.get("/screenshot/:guildId/:channelId/:messageId", async (req, res) => {
        const { guildId, channelId, messageId } = req.params;

        // 1. 跳到指定頻道（renderer 端 function）
        await win.webContents.executeJavaScript(
            `history.pushState({}, "", "/channels/${guildId}/${channelId}/${messageId}"); window.dispatchEvent(new PopStateEvent("popstate"));`
        );

        // 2. 找到元素位置
        const rect = await win.webContents.executeJavaScript(`
        new Promise((resolve, reject) => {
            const id = "chat-messages-${channelId}-${messageId}";
            const el = document.getElementById(id);
            if (el) {
            const r = el.getBoundingClientRect();
            resolve({ x: r.x, y: r.y, width: r.width, height: r.height });
            return;
            }

            // 設定觀察器
            const observer = new MutationObserver(() => {
            const el2 = document.getElementById(id);
            if (el2) {
                const r2 = el2.getBoundingClientRect();
                observer.disconnect();
                setTimeout(() => {
                    resolve({ x: r2.x, y: r2.y, width: r2.width, height: r2.height });
                }, 500); // 等待動畫結束
            }
            });

            observer.observe(document.body, { childList: true, subtree: true });

            // timeout 10 秒
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

        // 3. 擷取該區域
        const image = await win.webContents.capturePage(rect);
        res.contentType("image/png");
        res.send(image.toPNG());
    });

    win.webContents.on("did-finish-load", () => {
        server.listen(3000, () => console.log("Server running on http://localhost:3000"));
    });

});
