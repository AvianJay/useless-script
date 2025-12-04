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
            if (statusBadge) statusBadge.textContent = `狀態: ${data.status}`;

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
});

function inviteBot(event) {
    if (event) event.preventDefault();
    if (botId) {
        const inviteUrl = `https://discord.com/oauth2/authorize?client_id=${botId}`;
        window.open(inviteUrl, "_blank");
    }
}