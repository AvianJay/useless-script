require("bytenode");
const { app, BrowserWindow } = require("electron");
const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { URL } = require("url");

let SocketIoServer = null;
try {
    // Optional dependency: if present, we prefer the real socket.io server.
    ({ Server: SocketIoServer } = require("socket.io"));
} catch (e) {
    SocketIoServer = null;
}

function createEngineIoPollingServer() {
    const sessions = new Map();

    const PING_INTERVAL_MS = 25000;
    const PING_TIMEOUT_MS = 20000;
    const LONG_POLL_TIMEOUT_MS = 20000;
    const MAX_PAYLOAD = 1_000_000;

    function now() {
        return Date.now();
    }

    function newSid() {
        return crypto.randomBytes(16).toString("hex");
    }

    function enqueue(sid, packet) {
        const session = sessions.get(sid);
        if (!session) return;
        session.queue.push(packet);
        if (session.pendingRes) {
            flushPoll(sid);
        }
    }

    function encodePayload(packets) {
        return packets.join("\x1e");
    }

    function flushPoll(sid) {
        const session = sessions.get(sid);
        if (!session || !session.pendingRes) return;

        const res = session.pendingRes;
        session.pendingRes = null;

        clearTimeout(session.pendingResTimeout);
        session.pendingResTimeout = null;

        const packets = session.queue.splice(0, session.queue.length);
        const payload = encodePayload(packets.length ? packets : ["6"]); // noop
        res.writeHead(200, {
            "Content-Type": "text/plain; charset=UTF-8",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        });
        res.end(payload);
    }

    function closeSession(sid, reason = "timeout") {
        const session = sessions.get(sid);
        if (!session) return;
        try {
            enqueue(sid, "1"); // close
        } catch (_) {
            // ignore
        }
        if (session.pendingRes) {
            try {
                session.pendingRes.writeHead(200, {
                    "Content-Type": "text/plain; charset=UTF-8",
                    "Cache-Control": "no-store",
                    "Access-Control-Allow-Origin": "*",
                });
                session.pendingRes.end("1");
            } catch (_) {
                // ignore
            }
        }
        clearInterval(session.pingTimer);
        clearTimeout(session.pendingResTimeout);
        sessions.delete(sid);
        console.log(`[socket.io] session closed sid=${sid} reason=${reason}`);
    }

    function ensureSession(sid) {
        const session = sessions.get(sid);
        if (!session) return null;
        return session;
    }

    function startPingLoop(sid) {
        const session = sessions.get(sid);
        if (!session) return;
        session.pingTimer = setInterval(() => {
            const s = sessions.get(sid);
            if (!s) return;
            if (now() - s.lastPongAt > PING_INTERVAL_MS + PING_TIMEOUT_MS) {
                return closeSession(sid, "ping timeout");
            }
            enqueue(sid, "2"); // ping
        }, PING_INTERVAL_MS);
    }

    async function readBody(req) {
        return await new Promise((resolve, reject) => {
            const chunks = [];
            let total = 0;
            req.on("data", (c) => {
                total += c.length;
                if (total > MAX_PAYLOAD) {
                    reject(new Error("payload too large"));
                    req.destroy();
                    return;
                }
                chunks.push(c);
            });
            req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
            req.on("error", reject);
        });
    }

    function parsePackets(payload) {
        if (!payload) return [];
        return String(payload).split("\x1e").filter(Boolean);
    }

    function handleSocketIo(req, res) {
        const urlObj = new URL(req.url, "http://127.0.0.1");
        const transport = urlObj.searchParams.get("transport");
        const eio = urlObj.searchParams.get("EIO");
        const sid = urlObj.searchParams.get("sid");

        if (eio !== "4" || transport !== "polling") {
            res.writeHead(400, { "Content-Type": "text/plain", "Access-Control-Allow-Origin": "*" });
            return res.end("Unsupported Engine.IO config");
        }

        // CORS preflight
        if (req.method === "OPTIONS") {
            res.writeHead(204, {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Max-Age": "86400",
            });
            return res.end();
        }

        if (req.method === "GET" && !sid) {
            const newSessionId = newSid();
            sessions.set(newSessionId, {
                sid: newSessionId,
                createdAt: now(),
                lastPongAt: now(),
                queue: [],
                pendingRes: null,
                pendingResTimeout: null,
                pingTimer: null,
                connected: false,
            });
            startPingLoop(newSessionId);

            const open = {
                sid: newSessionId,
                upgrades: [],
                pingInterval: PING_INTERVAL_MS,
                pingTimeout: PING_TIMEOUT_MS,
                maxPayload: MAX_PAYLOAD,
            };

            res.writeHead(200, {
                "Content-Type": "text/plain; charset=UTF-8",
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            });
            res.end("0" + JSON.stringify(open));
            console.log(`[socket.io] session opened sid=${newSessionId}`);
            return;
        }

        if (!sid || !ensureSession(sid)) {
            res.writeHead(400, { "Content-Type": "text/plain", "Access-Control-Allow-Origin": "*" });
            return res.end("Unknown sid");
        }

        if (req.method === "GET") {
            const session = sessions.get(sid);

            // If there is already a held long-poll response, finish it with a
            // noop so the previous HTTP request does not hang forever.  Also
            // clear the associated timeout to avoid it firing on the *new*
            // response later.
            if (session.pendingRes) {
                const stale = session.pendingRes;
                session.pendingRes = null;
                clearTimeout(session.pendingResTimeout);
                session.pendingResTimeout = null;
                try {
                    stale.writeHead(200, {
                        "Content-Type": "text/plain; charset=UTF-8",
                        "Cache-Control": "no-store",
                        "Access-Control-Allow-Origin": "*",
                    });
                    stale.end("6"); // noop
                } catch (_) { /* already closed */ }
            }

            // Register the *new* response BEFORE checking the queue so that
            // flushPoll() can actually write to it.
            session.pendingRes = res;

            // If the client drops the connection, clear pendingRes so we don't
            // try to write to a dead socket later.
            res.on("close", () => {
                if (session.pendingRes === res) {
                    session.pendingRes = null;
                    clearTimeout(session.pendingResTimeout);
                    session.pendingResTimeout = null;
                }
            });

            if (session.queue.length) {
                return flushPoll(sid);
            }

            // Nothing queued – hold the long-poll until data arrives or timeout.
            session.pendingResTimeout = setTimeout(() => {
                flushPoll(sid);
            }, LONG_POLL_TIMEOUT_MS);
            return;
        }

        if (req.method === "POST") {
            readBody(req)
                .then((payload) => {
                    const packets = parsePackets(payload);
                    const session = sessions.get(sid);
                    for (const p of packets) {
                        // Engine.IO packet types: 0 open, 1 close, 2 ping, 3 pong, 4 message, 6 noop
                        const type = p[0];
                        if (type === "3") {
                            session.lastPongAt = now();
                            continue;
                        }
                        if (type === "1") {
                            closeSession(sid, "client close");
                            continue;
                        }
                        if (type === "4") {
                            // Socket.IO payload: 0 connect, 2 event, etc. We only need connect.
                            const sioType = p[1];
                            if (sioType === "0") {
                                session.connected = true;
                                enqueue(sid, "40");
                                console.log(`[socket.io] connected sid=${sid}`);
                            }
                        }
                    }
                    res.writeHead(200, {
                        "Content-Type": "text/plain; charset=UTF-8",
                        "Cache-Control": "no-store",
                        "Access-Control-Allow-Origin": "*",
                    });
                    res.end("ok");
                })
                .catch((err) => {
                    res.writeHead(413, { "Content-Type": "text/plain", "Access-Control-Allow-Origin": "*" });
                    res.end(String(err));
                });
            return;
        }

        res.writeHead(405, { "Content-Type": "text/plain", "Access-Control-Allow-Origin": "*" });
        res.end("Method not allowed");
    }

    function broadcast(eventName, data) {
        const payload = "42" + JSON.stringify([eventName, data]);
        for (const [sid, session] of sessions.entries()) {
            if (!session.connected) continue;
            enqueue(sid, payload);
        }
    }

    return {
        handleSocketIo,
        broadcast,
        sessions,
    };
}

function createDomTimeWatcher({ broadcast }) {
    let lastWarning = null;
    let lastReport = null;
    let timer = null;

    const SCRIPT = `
        (function() {
            try {
                const get = (id) => document.getElementById(id)?.innerText?.trim() ?? "";
                const pad2 = (s) => String(s || "").padStart(2, "0");

                const warning = (() => {
                    const y = get("warning-year");
                    const m = get("warning-month");
                    const d = get("warning-date");
                    const hh = get("warning-hour");
                    const mm = get("warning-minute");
                    const ss = get("warning-second");
                    if (!y || y === "-" || !m || m === "-" || !d || d === "-" || !hh || hh === "-" || !mm || mm === "-" || !ss || ss === "-") return null;
                    const time = y + "-" + pad2(m) + "-" + pad2(d) + " " + pad2(hh) + ":" + pad2(mm) + ":" + pad2(ss);
                    return { time, parts: { year: y, month: m, date: d, hour: hh, minute: mm, second: ss } };
                })();

                const report = (() => {
                    const y = get("report-year");
                    const m = get("report-month");
                    const d = get("report-date");
                    const hh = get("report-hour");
                    const mm = get("report-minute");
                    const ss = get("report-second");
                    if (!y || y === "-" || !m || m === "-" || !d || d === "-" || !hh || hh === "-" || !mm || mm === "-" || !ss || ss === "-") return null;
                    const time = y + "-" + pad2(m) + "-" + pad2(d) + " " + pad2(hh) + ":" + pad2(mm) + ":" + pad2(ss);
                    return { time, parts: { year: y, month: m, date: d, hour: hh, minute: mm, second: ss } };
                })();

                return { ok: true, warning, report, url: location.href };
            } catch (e) {
                return { ok: false, error: String(e) };
            }
        })();
    `;

    async function tick() {
        try {
            const win = await getWindow();
            if (!win) return;
            const r = await win.webContents.executeJavaScript(SCRIPT);
            if (!r || !r.ok) return;

            if (r.warning?.time && r.warning.time !== lastWarning) {
                lastWarning = r.warning.time;
                broadcast("warningTimeChanged", { time: r.warning.time, parts: r.warning.parts, url: r.url });
            }
            if (r.report?.time && r.report.time !== lastReport) {
                lastReport = r.report.time;
                broadcast("reportTimeChanged", { time: r.report.time, parts: r.report.parts, url: r.url });
            }
        } catch (_) {
            // ignore
        }
    }

    return {
        start(pollMs = 500) {
            if (timer) return;
            timer = setInterval(tick, pollMs);
            tick();
        },
        stop() {
            if (!timer) return;
            clearInterval(timer);
            timer = null;
        },
    };
}

async function handleGetWarningInfo(req, res) {
    const win = await getWindow();
    if (!win) {
        res.writeHead(500, { "Content-Type": "text/plain" });
        return res.end("No window");
    }

    const SCRIPT = `
        (function() {
            return new Promise((resolve) => {
                try {
                    const timeText = (() => {
                        const el = document.getElementById("warning-year");
                        if (!el) return null;
                        const y = document.getElementById("warning-year").innerText;
                        const m = document.getElementById("warning-month").innerText;
                        const d = document.getElementById("warning-date").innerText;
                        const hh = document.getElementById("warning-hour").innerText;
                        const mm = document.getElementById("warning-minute").innerText;
                        const ss = document.getElementById("warning-second").innerText;
                        return \`\${y}-\${m.padStart(2,'0')}-\${d.padStart(2,'0')} \${hh.padStart(2,'0')}:\${mm.padStart(2,'0')}:\${ss.padStart(2,'0')}\`;
                    })();

                    const locationText = (() => {
                        const lat = document.getElementById("warning-latitude")?.innerText ?? "";
                        const lon = document.getElementById("warning-longitude")?.innerText ?? "";
                        return { latitude: lat, longitude: lon, text: \`北緯 \${lat} / 東經 \${lon}\` };
                    })();

                    const depth = document.getElementById("warning-depth")?.innerText ?? null;
                    const magnitude = document.getElementById("warning-magnitude")?.innerText ?? null;
                    const maxIntensity = document.getElementById("warning-max-intensity")?.innerText ?? null;
                    const intensity = document.getElementById("warning-intensity")?.innerText ?? null;
                    const eta = document.getElementById("warning-eta")?.innerText ?? null;

                    // 解析警報列表（多列）
                    const list = [];
                    const listBody = document.querySelector("#warning-list .body");
                    if (listBody) {
                        const rows = Array.from(listBody.querySelectorAll("div > div"));
                        if (rows.length === 0) {
                        // Fallback
                        const innerRows = listBody.querySelectorAll("div");
                        innerRows.forEach(r => {
                            const cols = Array.from(r.querySelectorAll(".content")).map(c => c.innerText.trim());
                            if (cols.length >= 5) {
                            list.push({
                                id: cols[0],
                                time: cols[1],
                                latlon: cols[2],
                                depth: cols[3],
                                mag: cols[4]
                            });
                            }
                        });
                        } else {
                        rows.forEach(r => {
                            const cols = Array.from(r.querySelectorAll(".content")).map(c => c.innerText.trim());
                            if (cols.length >= 5) {
                            list.push({
                                id: cols[0],
                                time: cols[1],
                                latlon: cols[2],
                                depth: cols[3],
                                mag: cols[4]
                            });
                            }
                        });
                        }
                    }

                    resolve({
                        ok: true,
                        time: timeText,
                        location: locationText,
                        depth: depth,
                        magnitude: magnitude,
                        maxIntensity: maxIntensity,
                        intensity: intensity,
                        eta: eta,
                        list: list,
                        url: location.href
                    });
                } catch (e) {
                    resolve({ ok: false, error: String(e) });
                }
            });
        })();
    `;

    try {
        const result = await win.webContents.executeJavaScript(SCRIPT, true /* userGesture optional */);

        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify(result));
    } catch (err) {
        console.error("executeJavaScript error:", err);
        res.writeHead(500, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ ok: false, error: String(err) }));
    }
}

async function handleGetReportInfo(req, res) {
    const win = await getWindow();
    if (!win) {
        res.writeHead(500, { "Content-Type": "text/plain" });
        return res.end("No window");
    }
    const SCRIPT = `
        (function() {
            return new Promise((resolve) => {
                try {
                    const getText = (id) => document.getElementById(id)?.innerText.trim() || "";

                    const year = getText("report-year");
                    const month = getText("report-month").padStart(2, "0");
                    const date = getText("report-date").padStart(2, "0");
                    const hour = getText("report-hour").padStart(2, "0");
                    const minute = getText("report-minute").padStart(2, "0");
                    const second = getText("report-second").padStart(2, "0");

                    const data = {
                        number: getText("report-number"),
                        time: year + "-" + month + "-" + date + " " + hour + ":" + minute + ":" + second,
                        latitude: getText("report-latitude"),
                        longitude: getText("report-longitude"),
                        depth: getText("report-depth"),
                        magnitude: getText("report-magnitude"),
                        maxIntensity: getText("report-max-intensity"),
                        intensities: []
                    }

                    // 各地震度
                    const areas = document.querySelectorAll("#report-intensity .intensity-button");
                    areas.forEach(area => {
                        const wrapper = area.querySelector(".area-max-intensity");

                        const right = wrapper.querySelector("div[style*='float: right']"); // 右側震度
                        const maxIntensity = right?.innerText.trim() || "";

                        // remove right element to get area name only
                        if (right) right.remove();
                        const areaName = wrapper.innerText.trim();

                        const rows = area.querySelectorAll(".station-intensity tr[intensity]");
                        const stations = [];
                        rows.forEach(r => {
                            const level = r.getAttribute("intensity");
                            const names = [];
                            r.querySelectorAll(".station span").forEach(s => names.push(s.innerText.trim()));
                            stations.push({ level, names });
                        });

                        data.intensities.push({
                            area: areaName,
                            maxIntensity,
                            stations
                        });
                    });

                    resolve({ ok: true, report: data });
                } catch (e) {
                    resolve({ ok: false, error: String(e) });
                }
            });
        })();
    `;
    try {
        const result = await win.webContents.executeJavaScript(SCRIPT, true /* userGesture optional */);

        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify(result));
    } catch (err) {
        console.error("executeJavaScript error:", err);
        res.writeHead(500, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ ok: false, error: String(err) }));
    }
}

async function getWindow(settings=false) {
    let win = BrowserWindow.getAllWindows()[0];
    if (!win) {
        return null;
    }
    CHECK_SCRIPT = `
        (function(){
            return (document.getElementsByClassName("title")[0]?.innerText || "").includes("設定")
        })();
    `;
    let isSettings = await win.webContents.executeJavaScript(CHECK_SCRIPT);
    if (settings && isSettings) {
        return win;
    } else if (!settings && !isSettings) {
        return win;
    } else if (settings && !isSettings) {
        win = BrowserWindow.getAllWindows()[1];
        if (!win) {
            win = BrowserWindow.getAllWindows()[0];
            OPEN_SETTINGS_SCRIPT = `
                (function(){
                    document.getElementById("setting-icon").click();
                })();
            `;
            await win.webContents.executeJavaScript(OPEN_SETTINGS_SCRIPT);
            await new Promise(resolve => setTimeout(resolve, 1000)); // 等待視窗打開
            return await getWindow(true);
        };
        isSettings = await win.webContents.executeJavaScript(CHECK_SCRIPT);
        if (isSettings) return win;
        return null;
    } else if (!settings && isSettings) {
        win = BrowserWindow.getAllWindows()[1];
        if (!win) return null;
        isSettings = await win.webContents.executeJavaScript(CHECK_SCRIPT);
        if (!isSettings) return win;
        return null;
    }
}

function startHttpServer() {
    let fallbackEio = null;
    let broadcast = null;

    const server = http.createServer(async (req, res) => {
        // If socket.io is enabled, do not handle its path here.
        if (SocketIoServer && req.url && req.url.startsWith("/socket.io/")) {
            return;
        }
        // Fallback to minimal Engine.IO polling implementation.
        if (!SocketIoServer && req.url && req.url.startsWith("/socket.io/")) {
            return fallbackEio.handleSocketIo(req, res);
        }
        if (req.url === "/") {
            res.writeHead(200, { "Content-Type": "text/plain" });
            return res.end("OXWU API");
        } else if (req.url === "/screenshot") {
            const win = await getWindow();
            if (!win) {
                res.writeHead(500);
                return res.end("No window");
            }
            win.show();
            const image = await win.webContents.capturePage();
            res.writeHead(200, { "Content-Type": "image/png" });
            res.end(image.toPNG());
        } else if (req.url === "/gotoReport") {
            const win = await getWindow();
            if (!win) {
                res.writeHead(500);
                return res.end("No window");
            }
            SCRIPT = "document.querySelector('[target=report]').click();"
            win.webContents.executeJavaScript(SCRIPT);
            res.writeHead(200);
            res.end("{\"status\":\"navigated\"}");
        } else if (req.url === "/gotoWarning") {
            const win = await getWindow();
            if (!win) {
                res.writeHead(500);
                return res.end("No window");
            }
            SCRIPT = "document.querySelector('[target=warning]').click();"
            win.webContents.executeJavaScript(SCRIPT);
            res.writeHead(200);
            res.end("{\"status\":\"navigated\"}");
        } else if (req.url === "/injectEruda") {
            const win = await getWindow();
            if (!win) {
                res.writeHead(500);
                return res.end("No window");
            }
            SCRIPT = `
                (function(){
                    if (window.__ERUDA_INJECTED__) return;
                    window.__ERUDA_INJECTED__ = true;

                    var s = document.createElement('script');
                    s.src = 'https://cdn.jsdelivr.net/npm/eruda';
                    s.onload = function(){
                        eruda.init();
                        console.log('[INJECT] eruda loaded');
                    };
                    s.onerror = function(e){ console.error('[INJECT] eruda failed', e); };
                    document.head.appendChild(s);
                })();
            `
            win.webContents.executeJavaScript(SCRIPT);
            res.writeHead(200);
            res.end("{\"status\":\"injected\"}");
        } else if (req.url === "/injectErudaSettings") {
            const winSettings = await getWindow(true);
            if (!winSettings) {
                res.writeHead(500);
                return res.end("No window");
            }
            SCRIPT = `
                (function(){
                    if (window.__ERUDA_INJECTED__) return;
                    window.__ERUDA_INJECTED__ = true;

                    var s = document.createElement('script');
                    s.src = 'https://cdn.jsdelivr.net/npm/eruda';
                    s.onload = function(){
                        eruda.init();
                        console.log('[INJECT] eruda loaded');
                    };
                    s.onerror = function(e){ console.error('[INJECT] eruda failed', e); };
                    document.head.appendChild(s);
                })();
            `
            winSettings.webContents.executeJavaScript(SCRIPT);
            res.writeHead(200);
            res.end("{\"status\":\"injected\"}");
        } else if (req.url === "/getWarningInfo") {
            handleGetWarningInfo(req, res);
        } else if (req.url === "/getReportInfo") {
            handleGetReportInfo(req, res);
        } else if (req.url === "/openSettings") {
            await getWindow(true);
            res.writeHead(200);
            res.end("{\"status\":\"opened\"}");
        } else if (req.url === "/closeSettings") {
            const win = await getWindow(true);
            if (win) win.close();
            res.writeHead(200);
            res.end("{\"status\":\"closed\"}");
        } else {
            res.writeHead(404);
            res.end("Not found");
        }
    });

    // Initialize socket layer
    if (SocketIoServer) {
        const io = new SocketIoServer(server, {
            path: "/socket.io/",
            cors: { origin: "*" },
        });
        io.on("connection", (socket) => {
            console.log(`[socket.io] client connected id=${socket.id}`);
            socket.on("disconnect", (reason) => {
                console.log(`[socket.io] client disconnected id=${socket.id} reason=${reason}`);
            });
        });
        broadcast = (eventName, data) => io.emit(eventName, data);
        console.log("[socket.io] using official socket.io server");
    } else {
        fallbackEio = createEngineIoPollingServer();
        broadcast = fallbackEio.broadcast;
        console.log("[socket.io] socket.io package not found; using polling-only fallback");
        console.log("[socket.io] To use official socket.io: install it into OXWU app folder or copy node_modules along with this patch.");
    }

    const watcher = createDomTimeWatcher({ broadcast });

    server.listen(10281, "127.0.0.1", () => {
        console.log("HTTP server listening on http://127.0.0.1:10281");
        console.log("Socket.IO endpoint: http://127.0.0.1:10281/socket.io/");
        watcher.start(500);
    });
}

app.on("ready", () => {
    startHttpServer();
});

module.exports = require("./main.jsc");