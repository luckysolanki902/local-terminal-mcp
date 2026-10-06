const textFields = ["baseUrl", "mcpPath", "folder"];
const defaults = {
  baseUrl: "http://localhost:3003",
  mcpPath: "",
  folder: "incoming",
  autoSave: false,
};

chrome.storage.local.get(defaults, (cfg) => {
  for (const f of textFields) document.getElementById(f).value = cfg[f] || "";
  document.getElementById("autoSave").checked = !!cfg.autoSave;
});

document.getElementById("save").addEventListener("click", () => {
  const cfg = {};
  for (const f of textFields) cfg[f] = document.getElementById(f).value.trim();
  cfg.autoSave = document.getElementById("autoSave").checked;
  // Normalise: strip trailing slash from baseUrl, ensure mcpPath starts with /.
  cfg.baseUrl = cfg.baseUrl.replace(/\/+$/, "");
  if (cfg.mcpPath && !cfg.mcpPath.startsWith("/")) cfg.mcpPath = "/" + cfg.mcpPath;
  chrome.storage.local.set(cfg, () => {
    document.getElementById("status").textContent = "Saved.";
    setTimeout(() => (document.getElementById("status").textContent = ""), 1500);
  });
});
