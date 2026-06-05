import { invoke, isTauri } from "@tauri-apps/api/core";

/**
 * Download (or open) a file the agent sent via send_file_to_user.
 *
 * The chat runs from the backend-hosted console (http://127.0.0.1:<port>),
 * so file URLs are same-origin `/files/preview/...` links whose response sets
 * `Content-Disposition: attachment`. Two environments must be handled:
 *
 * - Tauri desktop: the webview swallows `window.open` for these URLs. When the
 *   remote capability has enabled IPC on the console origin, `open_external`
 *   hands the URL to the system browser, which downloads the attachment.
 * - Plain browser (and as a fallback when IPC is unavailable): trigger a
 *   same-origin download via an anchor element, which WebView2/Chromium honor.
 *
 * @param url - File URL, absolute or root-relative (e.g. `/files/preview/...`).
 * @param filename - Suggested download filename.
 */
export async function downloadFile(
  url: string,
  filename?: string,
): Promise<void> {
  if (!url) return;

  const fullUrl = url.startsWith("http")
    ? url
    : `${window.location.origin}${url}`;

  if (isTauri()) {
    try {
      await invoke("open_external", { url: fullUrl });
      return;
    } catch {
      // IPC unavailable on this page — fall through to the anchor download.
    }
  }

  const a = document.createElement("a");
  a.href = fullUrl;
  if (filename) a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
