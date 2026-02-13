function secondsToDhms(seconds) {
    seconds = Number(seconds);
    var d = Math.floor(seconds / (3600 * 24));
    var h = Math.floor((seconds % (3600 * 24)) / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = Math.floor(seconds % 60);
    var dDisplay = d > 0 ? d + (d == 1 ? " 天, " : " 天, ") : "";
    var hDisplay = h > 0 ? h + (h == 1 ? " 小時, " : " 小時, ") : "";
    var mDisplay = m > 0 ? m + (m == 1 ? " 分鐘, " : " 分鐘, ") : "";
    var sDisplay = s > 0 ? s + (s == 1 ? " 秒" : " 秒") : "";
    return dDisplay + hDisplay + mDisplay + sDisplay;
}

let uptimeSeconds = 0;
function updateUptime() {
    uptimeSeconds++;
    const uptimeEl = document.getElementById("stat-uptime");
    if (uptimeEl) {
        uptimeEl.textContent = secondsToDhms(uptimeSeconds);
    }
}

let botId = null;

document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/status")
        .then(response => response.json())
        .then(data => {
            document.title = `${data.name} | Discord 機器人`;

            // Update Hero Section
            const avatarEl = document.getElementById("botavatar");
            if (avatarEl) avatarEl.src = data.avatar_url || "";

            const nameEl = document.getElementById("botname");
            if (nameEl) nameEl.textContent = data.name;

            const statusBadge = document.getElementById("botstatus-badge");
            if (statusBadge) {
                switch (data.status) {
                    case "online":
                        statusBadge.style.backgroundColor = "#43b581";
                        statusBadge.textContent = "狀態: 在線";
                        break;
                    case "starting":
                        statusBadge.style.backgroundColor = "#faa61a";
                        statusBadge.textContent = "狀態: 啟動中";
                        break;
                    case "offline":
                        statusBadge.style.backgroundColor = "#f04747";
                        statusBadge.textContent = "狀態: 離線";
                        break;
                }
            }

            // Update Navbar
            const navAvatar = document.getElementById("nav-avatar");
            if (navAvatar) {
                navAvatar.src = data.avatar_url || "";
                navAvatar.style.display = "inline-block";
            }
            const navName = document.getElementById("nav-name");
            if (navName) navName.textContent = data.name;

            // Update Stats Grid
            const statServers = document.getElementById("stat-servers");
            if (statServers) statServers.textContent = data.server_count;

            const statUsers = document.getElementById("stat-users");
            if (statUsers) statUsers.textContent = data.user_count;

            const statInstall = document.getElementById("stat-install");
            if (statInstall) statInstall.textContent = data.user_install_count;

            const statPing = document.getElementById("stat-ping");
            if (statPing) statPing.textContent = `${data.latency_ms}ms`;

            const statVersion = document.getElementById("stat-version");
            if (statVersion) statVersion.textContent = data.version;

            uptimeSeconds = data.uptime;
            updateUptime();
            setInterval(updateUptime, 1000);

            botId = data.id;
            if (botId) {
                const inviteBtn = document.getElementById("invitebtn");
                if (inviteBtn) {
                    inviteBtn.classList.remove("hidden");
                    inviteBtn.href = `https://discord.com/oauth2/authorize?client_id=${botId}`;
                }
            }
        })
        .catch(error => {
            console.error("Error fetching status:", error);
            const statusBadge = document.getElementById("botstatus-badge");
            if (statusBadge) statusBadge.textContent = "Error";
        });

    // Fetch Commit Logs
    fetch("/api/commit_logs")
        .then(response => response.json())
        .then(data => {
            const logsContainer = document.getElementById("commit-logs");
            if (logsContainer && data.commit_logs) {
                if (data.commit_logs.length === 0 || (data.commit_logs.length === 1 && data.commit_logs[0] === "N/A")) {
                    logsContainer.innerHTML = "<p style='text-align: center; color: #888;'>暫無更新記錄</p>";
                    return;
                }

                logsContainer.innerHTML = ""; // Clear loading message
                data.commit_logs.forEach(logStr => {
                    const item = document.createElement("div");
                    item.className = "commit-item";
                    item.style.cssText = "background: rgba(255,255,255,0.05); padding: 15px; margin-bottom: 10px; border-radius: 8px; border-left: 4px solid var(--primary-color, #5865F2);";

                    // Parse log string: Author: Hash - Message (Relative Date)
                    // Example: AvianJay: a1b2c3d - Fix bug (2 days ago)
                    const match = logStr.match(/^(.*?): (.*?) - (.*?) \((.*?)\)$/);

                    if (match) {
                        const [full, author, hash, message, date] = match;
                        item.innerHTML = `
                            <div class="commit-header" style="font-size: 0.9em; color: #bbb; margin-bottom: 4px; display: flex; justify-content: space-between;">
                                <span><strong style="color: var(--text-primary, #fff);">${author}</strong> <span style="opacity: 0.7;">提交 ${hash}</span></span>
                                <span style="opacity: 0.7;">${date}</span>
                            </div>
                            <div class="commit-message" style="font-size: 1.1em; color: var(--text-primary, #eee);">${message}</div>
                        `;
                    } else {
                        // Fallback for unexpected format
                        item.textContent = logStr;
                        item.style.color = "var(--text-primary, #eee)";
                    }
                    logsContainer.appendChild(item);
                });
            }
        })
        .catch(error => {
            console.error("Error fetching commit logs:", error);
            const logsContainer = document.getElementById("commit-logs");
            if (logsContainer) logsContainer.innerHTML = "<p style='text-align: center; color: #ff5555;'>無法載入更新記錄</p>";
        });
});

function inviteBot(event) {
    gtag('event', 'invite_bot', {
        'event_category': 'engagement',
        'event_label': 'Invite Bot Button Clicked'
    });
    if (event) event.preventDefault();
    if (botId) {
        const inviteUrl = `https://discord.com/oauth2/authorize?client_id=${botId}`;
        window.open(inviteUrl, "_blank");
    }
}