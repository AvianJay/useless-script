require("bytenode");
const { app, BrowserWindow } = require("electron");
const http = require("http");
const fs = require("fs");
const path = require("path");

async function handleGetWarningInfo(req, res) {
    const win = BrowserWindow.getAllWindows()[0];
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
    const win = BrowserWindow.getAllWindows()[0];
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
                    };

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

function startHttpServer() {
    http.createServer(async (req, res) => {
        if (req.url === "/screenshot") {
            const win = BrowserWindow.getAllWindows()[0];
            if (!win) {
                res.writeHead(500);
                return res.end("No window");
            }
            const image = await win.webContents.capturePage();
            res.writeHead(200, { "Content-Type": "image/png" });
            res.end(image.toPNG());
        } else if (req.url === "/gotoReport") {
            const win = BrowserWindow.getAllWindows()[0];
            if (!win) {
                res.writeHead(500);
                return res.end("No window");
            }
            SCRIPT = "document.querySelector('[target=report]').click();"
            win.webContents.executeJavaScript(SCRIPT);
            res.writeHead(200);
            res.end("{\"status\":\"navigated\"}");
        } else if (req.url === "/gotoWarning") {
            const win = BrowserWindow.getAllWindows()[0];
            if (!win) {
                res.writeHead(500);
                return res.end("No window");
            }
            SCRIPT = "document.querySelector('[target=warning]').click();"
            win.webContents.executeJavaScript(SCRIPT);
            res.writeHead(200);
            res.end("{\"status\":\"navigated\"}");
        } else if (req.url === "/injectEruda") {
            const win = BrowserWindow.getAllWindows()[0];
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
        } else if (req.url === "/getWarningInfo") {
            handleGetWarningInfo(req, res);
        } else if (req.url === "/getReportInfo") {
            handleGetReportInfo(req, res);
        } else {
            res.writeHead(404);
            res.end("Not found");
        }
    }).listen(10281, "127.0.0.1", () => {
        console.log("HTTP server listening on http://127.0.0.1:10281");
    });
}

app.on("ready", () => {
    startHttpServer();
});

module.exports = require("./main.jsc");