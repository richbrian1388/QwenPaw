/**
 * Download a file the agent sent via send_file_to_user.
 *
 * The chat runs from the backend-hosted console, so file URLs are same-origin
 * `/api/files/preview/...` links whose response sets `Content-Disposition:
 * attachment`. Navigating to such a URL downloads it without leaving the page:
 *
 * - Plain browser: the attachment header makes the browser download the file
 *   while keeping the current page.
 * - Tauri desktop: the webview can't save downloads itself, so a Rust
 *   `on_navigation` handler (src-tauri/src/lib.rs) intercepts these
 *   `/files/preview/` navigations and opens them in the system browser, which
 *   downloads the file, and cancels the in-webview navigation.
 *
 * @param url - File URL, absolute or root-relative (e.g. `/api/files/preview/...`).
 */
export function downloadFile(url: string): void {
  if (!url) return;

  const fullUrl = url.startsWith("http")
    ? url
    : `${window.location.origin}${url}`;

  window.location.assign(fullUrl);
}
