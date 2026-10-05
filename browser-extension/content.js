// Content script: adds a floating button that saves the ChatGPT-generated
// images on the page into your local repo via the local-terminal-mcp /upload
// endpoint. The browser already holds the rendered image bytes (as blobs), so
// we just fetch them and POST them to the local server.

(function () {
  "use strict";

  function getConfig() {
    return new Promise((resolve) => {
      chrome.storage.local.get(
        { baseUrl: "http://localhost:3003", mcpPath: "", folder: "assets" },
        resolve
      );
    });
  }

  // Generated images render as blob: URLs (or oaiusercontent URLs).
  function generatedImageSrcs() {
    const srcs = [...document.querySelectorAll("img")]
      .map((img) => img.src)
      .filter((s) => s && (s.startsWith("blob:") || s.includes("oaiusercontent")));
    return [...new Set(srcs)];
  }

  function toast(msg, ok) {
    let el = document.getElementById("mcp-saver-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "mcp-saver-toast";
      el.style.cssText =
        "position:fixed;bottom:76px;right:20px;z-index:2147483647;" +
        "padding:10px 14px;border-radius:10px;font:13px system-ui;" +
        "color:#fff;max-width:320px;box-shadow:0 4px 16px rgba(0,0,0,.3)";
      document.body.appendChild(el);
    }
    el.style.background = ok ? "#166534" : "#991b1b";
    el.textContent = msg;
    el.style.opacity = "1";
    setTimeout(() => (el.style.opacity = "0"), 4000);
  }

  async function saveOne(src, name, cfg) {
    const blob = await fetch(src).then((r) => r.blob());
    const ext = (blob.type.split("/")[1] || "png").replace("jpeg", "jpg");
    const path = `${cfg.folder || "assets"}/${name}.${ext}`;
    const url = `${cfg.baseUrl}${cfg.mcpPath}/upload?path=${encodeURIComponent(
      path
    )}`;
    const resp = await fetch(url, { method: "POST", body: blob });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json();
  }

  async function saveAll() {
    const cfg = await getConfig();
    if (!cfg.mcpPath) {
      toast("Set the MCP secret path in the extension popup first.", false);
      return;
    }
    const srcs = generatedImageSrcs();
    if (!srcs.length) {
      toast("No generated images found on this page.", false);
      return;
    }
    const stamp = Date.now();
    let saved = 0;
    for (let i = 0; i < srcs.length; i++) {
      try {
        const res = await saveOne(srcs[i], `gpt-${stamp}-${i + 1}`, cfg);
        if (res.ok) saved++;
      } catch (e) {
        toast(`Failed on image ${i + 1}: ${e.message}`, false);
        return;
      }
    }
    toast(`Saved ${saved}/${srcs.length} image(s) to your repo.`, true);
  }

  function addButton() {
    if (document.getElementById("mcp-saver-btn")) return;
    const btn = document.createElement("button");
    btn.id = "mcp-saver-btn";
    btn.textContent = "⬇ Save images → repo";
    btn.style.cssText =
      "position:fixed;bottom:20px;right:20px;z-index:2147483647;" +
      "padding:10px 14px;border:none;border-radius:999px;cursor:pointer;" +
      "font:600 13px system-ui;color:#fff;background:#10a37f;" +
      "box-shadow:0 4px 16px rgba(0,0,0,.3)";
    btn.addEventListener("click", saveAll);
    document.body.appendChild(btn);
  }

  addButton();
  // ChatGPT is a SPA; keep the button present across navigations.
  new MutationObserver(addButton).observe(document.body, {
    childList: true,
    subtree: false,
  });
})();
