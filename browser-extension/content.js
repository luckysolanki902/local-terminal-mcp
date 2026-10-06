// Content script: saves ChatGPT-generated images into your local repo via the
// local-terminal-mcp /upload endpoint. The browser already holds the rendered
// image bytes (as blobs), so we fetch them and hand them to the background
// worker, which POSTs them to the local server.
//
// Two modes:
//   - Manual: click the floating "Save images -> repo" button.
//   - Auto-save (toggle in the popup): new generated images are uploaded
//     automatically as they appear — no clicks, no MCP involvement needed.

(function () {
  "use strict";

  const uploaded = new Set(); // srcs already uploaded this session

  function getConfig() {
    return new Promise((resolve) => {
      chrome.storage.local.get(
        {
          baseUrl: "http://localhost:3003",
          mcpPath: "",
          folder: "incoming",
          autoSave: false,
        },
        resolve
      );
    });
  }

  // Generated images render as blob: URLs (or oaiusercontent URLs).
  function generatedImages(minSide) {
    return [...document.querySelectorAll("img")].filter((img) => {
      const s = img.src || "";
      const big = (img.naturalWidth || 0) >= (minSide || 0);
      return (s.startsWith("blob:") || s.includes("oaiusercontent")) && big;
    });
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

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = reject;
      fr.readAsDataURL(blob);
    });
  }

  async function saveSrc(src, name, cfg) {
    const blob = await fetch(src).then((r) => r.blob());
    const ext = (blob.type.split("/")[1] || "png").replace("jpeg", "jpg");
    const path = `${cfg.folder || "incoming"}/${name}.${ext}`;
    const url = `${cfg.baseUrl}${cfg.mcpPath}/upload?path=${encodeURIComponent(
      path
    )}`;
    const dataUrl = await blobToDataUrl(blob);
    const res = await chrome.runtime.sendMessage({ type: "upload", url, dataUrl });
    if (!res || !res.ok) {
      throw new Error(res && (res.error || `HTTP ${res.status}: ${res.body}`));
    }
    return res;
  }

  // -- manual: save every generated image on the page ---------------------

  async function saveAll() {
    const cfg = await getConfig();
    if (!cfg.mcpPath) {
      toast("Set the MCP secret path in the extension popup first.", false);
      return;
    }
    const imgs = generatedImages(0);
    if (!imgs.length) {
      toast("No generated images found on this page.", false);
      return;
    }
    const stamp = Date.now();
    let saved = 0;
    for (let i = 0; i < imgs.length; i++) {
      try {
        const res = await saveSrc(imgs[i].src, `gpt-${stamp}-${i + 1}`, cfg);
        if (res.ok) {
          saved++;
          uploaded.add(imgs[i].src);
        }
      } catch (e) {
        toast(`Failed on image ${i + 1}: ${e.message}`, false);
        return;
      }
    }
    toast(`Saved ${saved}/${imgs.length} image(s) to your repo.`, true);
  }

  // -- auto-save: upload new generated images as they appear --------------

  let autoBusy = false;
  async function autoScan() {
    const cfg = await getConfig();
    if (!cfg.autoSave || !cfg.mcpPath || autoBusy) return;
    // Only full-size images (skip tiny thumbnails/icons), not yet uploaded.
    const fresh = generatedImages(400).filter((im) => !uploaded.has(im.src));
    if (!fresh.length) return;
    autoBusy = true;
    try {
      for (const im of fresh) {
        uploaded.add(im.src); // mark first to avoid double-fire
        try {
          await saveSrc(im.src, `gpt-auto-${Date.now()}`, cfg);
          toast("Auto-saved a generated image to your repo.", true);
        } catch (e) {
          uploaded.delete(im.src);
          toast(`Auto-save failed: ${e.message}`, false);
        }
      }
    } finally {
      autoBusy = false;
    }
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

  // ChatGPT is a SPA; keep the button present and watch for new images.
  let debounce;
  new MutationObserver(() => {
    addButton();
    clearTimeout(debounce);
    debounce = setTimeout(autoScan, 1200);
  }).observe(document.body, { childList: true, subtree: true });

  autoScan();
})();
