// Background service worker: performs the cross-origin POST to the local MCP
// server. Doing the upload here (not in the content script) is what makes it
// work — the service worker, with host_permissions, bypasses the ChatGPT page's
// CSP/CORS restrictions that would otherwise block a request to localhost.

function dataUrlToBytes(dataUrl) {
  const b64 = dataUrl.split(",")[1] || "";
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "upload") {
    (async () => {
      try {
        const bytes = dataUrlToBytes(msg.dataUrl);
        const resp = await fetch(msg.url, { method: "POST", body: bytes });
        const text = await resp.text();
        sendResponse({ ok: resp.ok, status: resp.status, body: text });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    })();
    return true; // keep the message channel open for the async response
  }
});
