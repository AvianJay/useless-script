const numberFormatter = new Intl.NumberFormat(window.I18N_LOCALE || "zh-TW");

function formatNumber(value) {
    const number = Number(value);
    return Number.isFinite(number)
        ? numberFormatter.format(number)
        : t("web.index.js.unavailable");
}

function secondsToDhms(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value)) return t("web.index.js.unavailable");
    const remaining = Math.max(0, Math.floor(value));
    const units = [
        ["day", Math.floor(remaining / 86400)],
        ["hour", Math.floor((remaining % 86400) / 3600)],
        ["minute", Math.floor((remaining % 3600) / 60)],
        ["second", remaining % 60],
    ];
    const parts = units
        .filter(function (entry, index) {
            return entry[1] > 0 || (index === units.length - 1 && units.slice(0, -1).every(item => item[1] === 0));
        })
        .map(function (entry) {
            return t("web.index.js.uptime." + entry[0], {
                count: numberFormatter.format(entry[1]),
            });
        });
    return parts.join(", ");
}

let uptimeSeconds = null;
function updateUptime() {
    const uptimeEl = document.getElementById("stat-uptime");
    if (uptimeEl) uptimeEl.textContent = secondsToDhms(uptimeSeconds);
    uptimeSeconds += 1;
}

function setCommitNotice(container, key, color) {
    const notice = document.createElement("p");
    notice.style.cssText = `text-align: center; color: ${color};`;
    notice.textContent = t(key);
    container.replaceChildren(notice);
}

function makeCommitItem(logStr) {
    const item = document.createElement("div");
    item.className = "commit-item";
    item.style.cssText = "background: rgba(255,255,255,0.05); padding: 15px; margin-bottom: 10px; border-radius: 8px; border-left: 4px solid var(--primary-color, #5865F2);";

    const match = logStr.match(/^(.*?): (.*?) - (.*?) \((.*?)\)$/);
    if (!match) {
        item.textContent = logStr;
        item.style.color = "var(--text-primary, #eee)";
        return item;
    }

    const [, author, hash, message, date] = match;
    const header = document.createElement("div");
    header.className = "commit-header";
    header.style.cssText = "font-size: 0.9em; color: #bbb; margin-bottom: 4px; display: flex; justify-content: space-between;";

    const authorBlock = document.createElement("span");
    const authorName = document.createElement("strong");
    authorName.style.color = "var(--text-primary, #fff)";
    authorName.textContent = author;
    const commitLabel = document.createElement("span");
    commitLabel.style.opacity = "0.7";
    commitLabel.textContent = " " + t("web.index.js.commit.label", { hash: hash });
    authorBlock.append(authorName, commitLabel);

    const relativeDate = document.createElement("span");
    relativeDate.style.opacity = "0.7";
    relativeDate.textContent = date;
    header.append(authorBlock, relativeDate);

    const commitMessage = document.createElement("div");
    commitMessage.className = "commit-message";
    commitMessage.style.cssText = "font-size: 1.1em; color: var(--text-primary, #eee);";
    commitMessage.textContent = message;
    item.append(header, commitMessage);
    return item;
}

let botId = null;

document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/status")
        .then(function (response) {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(function (data) {
            document.title = t("web.index.js.title", { name: data.name });

            const avatarEl = document.getElementById("botavatar");
            if (avatarEl) avatarEl.src = data.avatar_url || "";
            const nameEl = document.getElementById("botname");
            if (nameEl) nameEl.textContent = data.name;

            const statusBadge = document.getElementById("botstatus-badge");
            if (statusBadge) {
                const colors = {
                    online: "#43b581",
                    starting: "#faa61a",
                    offline: "#f04747",
                };
                const status = Object.prototype.hasOwnProperty.call(colors, data.status)
                    ? data.status
                    : "offline";
                statusBadge.style.backgroundColor = colors[status];
                statusBadge.textContent = t("web.index.js.status.label", {
                    status: t("web.index.js.status." + status),
                });
            }

            const navAvatar = document.getElementById("nav-avatar");
            if (navAvatar) {
                navAvatar.src = data.avatar_url || "";
                navAvatar.style.display = "inline-block";
            }
            const navName = document.getElementById("nav-name");
            if (navName) navName.textContent = data.name;

            const values = {
                "stat-servers": data.server_count,
                "stat-users": data.user_count,
                "stat-install": data.user_install_count,
            };
            Object.entries(values).forEach(function ([id, value]) {
                const element = document.getElementById(id);
                if (element) element.textContent = formatNumber(value);
            });

            const statPing = document.getElementById("stat-ping");
            if (statPing) {
                const latency = Number(data.latency_ms);
                statPing.textContent = Number.isFinite(latency)
                    ? `${numberFormatter.format(latency)} ms`
                    : t("web.index.js.unavailable");
            }
            const statVersion = document.getElementById("stat-version");
            if (statVersion) statVersion.textContent = data.version || t("web.index.js.unavailable");

            uptimeSeconds = Number(data.uptime);
            if (Number.isFinite(uptimeSeconds)) {
                updateUptime();
                setInterval(updateUptime, 1000);
            } else {
                const uptimeEl = document.getElementById("stat-uptime");
                if (uptimeEl) uptimeEl.textContent = t("web.index.js.unavailable");
            }

            botId = data.id;
            if (botId) {
                const inviteBtn = document.getElementById("invitebtn");
                if (inviteBtn) {
                    inviteBtn.classList.remove("hidden");
                    inviteBtn.href = `https://discord.com/oauth2/authorize?client_id=${botId}`;
                }
            }
        })
        .catch(function (error) {
            console.error("Error fetching status:", error);
            const statusBadge = document.getElementById("botstatus-badge");
            if (statusBadge) statusBadge.textContent = t("web.index.js.status.load_error");
        });

    fetch("/api/commit_logs")
        .then(function (response) {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(function (data) {
            const logsContainer = document.getElementById("commit-logs");
            if (!logsContainer || !Array.isArray(data.commit_logs)) return;
            if (data.commit_logs.length === 0 ||
                    (data.commit_logs.length === 1 && data.commit_logs[0] === "N/A")) {
                setCommitNotice(logsContainer, "web.index.js.commit.empty", "#888");
                return;
            }
            logsContainer.replaceChildren(...data.commit_logs.map(makeCommitItem));
        })
        .catch(function (error) {
            console.error("Error fetching commit logs:", error);
            const logsContainer = document.getElementById("commit-logs");
            if (logsContainer) {
                setCommitNotice(logsContainer, "web.index.js.commit.failed", "#ff5555");
            }
        });
});

function inviteBot(event) {
    gtag("event", "invite_bot", {
        event_category: "engagement",
        event_label: "Invite Bot Button Clicked",
    });
    if (event) event.preventDefault();
    if (botId) {
        const inviteUrl = `https://discord.com/oauth2/authorize?client_id=${botId}`;
        window.open(inviteUrl, "_blank");
    }
}
