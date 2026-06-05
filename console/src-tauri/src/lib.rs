mod backend;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_shell::ShellExt;

/// Opens a URL (including backend `http://127.0.0.1:<port>/files/...` URLs) in
/// the user's default system handler — i.e. the system browser.
///
/// The web console routes "download/open file" clicks and external links here
/// when running inside Tauri: the webview silently ignores `window.open` for
/// external and file URLs, and the legacy `window.pywebview` bridge that the
/// browser fallback relied on does not exist in the Tauri shell.
#[tauri::command]
#[allow(deprecated)]
fn open_external(app: tauri::AppHandle, url: String) -> Result<(), String> {
    app.shell().open(url, None).map_err(|err| err.to_string())
}

/// Creates the main window in code (rather than via `tauri.conf.json`) so we can
/// attach an `on_navigation` handler.
///
/// The chat UI runs from the backend-hosted console at
/// `http://127.0.0.1:<port>/console` — a remote origin where Tauri does not
/// inject its IPC. So "download file" clicks cannot reach a Rust command, and
/// the webview itself fetches the file but never saves it. Here we intercept
/// top-level navigations to the backend's `/files/preview/` download URLs and
/// hand them to the system browser (which downloads the attachment), cancelling
/// the in-webview navigation so the console SPA stays put.
#[allow(deprecated)]
fn create_main_window(app: &tauri::App) -> tauri::Result<()> {
    let handle = app.handle().clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("SXPaw Desktop")
        .inner_size(1280.0, 800.0)
        .min_inner_size(960.0, 600.0)
        .resizable(true)
        .on_navigation(move |url| {
            let is_loopback =
                matches!(url.host_str(), Some("127.0.0.1") | Some("localhost"));
            if is_loopback && url.path().contains("/files/preview/") {
                // Download URL: open in the system browser (which saves the
                // attachment) and cancel the in-webview navigation.
                let _ = handle.shell().open(url.as_str(), None);
                return false;
            }
            // Allow everything else: the initial app load and the console SPA.
            true
        })
        .build()?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let build_result = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            backend::backend_port,
            backend::backend_startup_error,
            backend::restart_backend,
            open_external,
        ])
        .manage(backend::BackendState::default())
        .setup(|app| {
            create_main_window(app)?;
            backend::setup(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // The app currently has a single "main" window, so closing it
            // is equivalent to quitting. If a multi-window mode is introduced,
            // make this window-count aware and keep the exit-event fallback.
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                backend::stop(window.app_handle());
            }
        })
        .build(tauri::generate_context!());

    match build_result {
        Ok(app) => {
            app.run(|app_handle, event| {
                if let RunEvent::ExitRequested { .. } = event {
                    backend::stop(app_handle);
                }
            });
        }
        Err(err) => {
            eprintln!("[QwenPaw Desktop] Fatal startup error: {err}");
            std::process::exit(1);
        }
    }
}
