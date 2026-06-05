import { invoke, isTauri } from "@tauri-apps/api/core";

/**
 * Open an external URL (or a backend file URL such as `/files/preview/...`),
 * using the Tauri shell in the desktop app, the legacy pywebview bridge in the
 * old desktop, or window.open in the browser.
 *
 * @param url - The URL to open
 * @param target - Target window name (default: "_blank")
 * @param features - Window features string (default: "noopener,noreferrer")
 */
export function openExternalLink(
  url: string,
  target: string = "_blank",
  features: string = "noopener,noreferrer",
): void {
  if (!url) return;

  // Resolve relative URLs to absolute (needed for the desktop shells, which
  // open the URL outside the WebView context).
  const fullUrl = url.startsWith("http")
    ? url
    : `${window.location.origin}${url}`;

  // Tauri desktop: the webview silently ignores window.open for external/file
  // URLs, so hand off to the native `open_external` command (system browser).
  if (isTauri()) {
    void invoke("open_external", { url: fullUrl }).catch(() => {
      // Last-resort fallback if the command is unavailable.
      window.open(fullUrl, target, features);
    });
    return;
  }

  const pywebview = (window as any).pywebview;
  if (pywebview?.api?.open_external_link) {
    // Legacy pywebview desktop: use the bridge to open in the system browser.
    pywebview.api.open_external_link(fullUrl);
  } else {
    // Web browser: use standard window.open
    window.open(fullUrl, target, features);
  }
}
