function jumpToChannel(guildId, channelId) {
  const path = `/channels/${guildId}/${channelId}`;
  history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
function injectCSS() {
  if (!document.querySelector("#dcapturer-style")) {
    const style = document.createElement("style");
    style.id = "dcapturer-style";
    style.textContent = `
      [data-flash="true"] {
        animation: none !important;
        background-color: transparent !important;
        width: fit-content !important;
      }
    `;
    document.head.appendChild(style);
  }
}

window.addEventListener("DOMContentLoaded", injectCSS);

// 保險：如果 Discord 把 <head> 重繪，監聽補回去
const observer = new MutationObserver(injectCSS);
observer.observe(document.documentElement, { childList: true, subtree: true });
