mod backend;

use tauri::{Manager, RunEvent, WindowEvent};
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
        .setup(backend::setup)
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
