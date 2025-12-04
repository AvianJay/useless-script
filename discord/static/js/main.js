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
    document.getElementById("uptimetext").textContent = `運行時間: ${secondsToDhms(uptimeSeconds)}`;
}

let botId = null;

document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/status")
        .then(response => response.json())
        .then(data => {
            document.title = data.name;
            document.getElementById("botavatar").src = data.avatar_url || "";
            document.getElementById("botstatus").innerHTML = `狀態: ${data.status}<br>伺服器數量: ${data.server_count}<br>用戶總數量: ${data.user_count}<br>用戶安裝數量: ${data.user_install_count}<br>版本: ${data.version}<br>延遲: ${data.latency_ms}ms`;
            uptimeSeconds = data.uptime;
            updateUptime();
            setInterval(updateUptime, 1000);
            botId = data.id;
            if (botId) {
                document.getElementById("invitebtn").style.display = "inline-block";
            }
        })
        .catch(error => {
            console.error("Error fetching status:", error);
            document.getElementById("botstatus").textContent = "Error";
        });
});

function inviteBot() {
    if (botId) {
        const inviteUrl = `https://discord.com/oauth2/authorize?client_id=${botId}`;
        window.open(inviteUrl, "_blank");
    }
}