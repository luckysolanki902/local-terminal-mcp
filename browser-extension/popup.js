const DEFAULTS = {
  baseUrl: "http://localhost:3003",
  mcpPath: "",
  folder: "gpt-images",
  autoSave: false,
};
const TEXT_FIELDS = ["baseUrl", "mcpPath", "folder"];
const $ = (id) => document.getElementById(id);

function flash(msg) {
  $("saved").textContent = msg;
  setTimeout(() => ($("saved").textContent = ""), 1400);
}

function normalize(cfg) {
  cfg.baseUrl = (cfg.baseUrl || "").replace(/\/+$/, "");
  if (cfg.mcpPath && !cfg.mcpPath.startsWith("/")) cfg.mcpPath = "/" + cfg.mcpPath;
  return cfg;
}

async function checkConnection(baseUrl) {
  const dot = $("dot");
  const txt = $("statusText");
  if (!baseUrl) {
    dot.className = "dot";
    txt.textContent = "not set";
    return;
  }
  try {
    const r = await fetch(baseUrl + "/healthz", { cache: "no-store" });
    if (r.ok) {
      dot.className = "dot ok";
      txt.textContent = "connected";
      return;
    }
    throw new Error();
  } catch {
    dot.className = "dot bad";
    txt.textContent = "offline";
  }
}

// Load current settings.
chrome.storage.local.get(DEFAULTS, (cfg) => {
  for (const f of TEXT_FIELDS) $(f).value = cfg[f] || "";
  $("autoSave").checked = !!cfg.autoSave;
  checkConnection(cfg.baseUrl);
});

// The toggle applies instantly — no Save needed.
$("autoSave").addEventListener("change", (e) => {
  chrome.storage.local.set({ autoSave: e.target.checked }, () =>
    flash(e.target.checked ? "Auto-save on" : "Auto-save off")
  );
});

// Save the text fields.
$("save").addEventListener("click", () => {
  const cfg = {};
  for (const f of TEXT_FIELDS) cfg[f] = $(f).value.trim();
  normalize(cfg);
  chrome.storage.local.set(cfg, () => {
    for (const f of TEXT_FIELDS) $(f).value = cfg[f];
    flash("Saved");
    checkConnection(cfg.baseUrl);
  });
});

// Manually trigger a save of the images on the current page.
$("saveNow").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  chrome.tabs.sendMessage(tab.id, { type: "saveNow" }, () => {
    void chrome.runtime.lastError; // ignore if no content script on this page
    flash("Saving images on page…");
  });
});
