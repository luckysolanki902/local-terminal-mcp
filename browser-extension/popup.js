const fields = ["baseUrl", "mcpPath", "folder"];

chrome.storage.local.get(
  { baseUrl: "http://localhost:3003", mcpPath: "", folder: "incoming" },
  (cfg) => {
    for (const f of fields) document.getElementById(f).value = cfg[f] || "";
  }
);

document.getElementById("save").addEventListener("click", () => {
  const cfg = {};
  for (const f of fields) cfg[f] = document.getElementById(f).value.trim();
  // Normalise: strip trailing slash from baseUrl, ensure mcpPath starts with /.
  cfg.baseUrl = cfg.baseUrl.replace(/\/+$/, "");
  if (cfg.mcpPath && !cfg.mcpPath.startsWith("/")) cfg.mcpPath = "/" + cfg.mcpPath;
  chrome.storage.local.set(cfg, () => {
    document.getElementById("status").textContent = "Saved.";
    setTimeout(() => (document.getElementById("status").textContent = ""), 1500);
  });
});
