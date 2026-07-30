#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(any(
    all(target_os = "macos", target_arch = "aarch64"),
    all(target_os = "macos", target_arch = "x86_64"),
    all(target_os = "windows", target_arch = "x86_64")
)))]
compile_error!("EnMotion Desktop supports only macOS arm64, macOS x64, and Windows x64.");

mod sidecar;
mod updater;

use sidecar::SidecarRuntime;
use tauri::Manager;
use updater::UpdateRuntime;

fn show_main_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[cfg(target_os = "macos")]
fn chinese_macos_menu<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> tauri::Result<tauri::menu::Menu<R>> {
    use tauri::menu::{
        AboutMetadata, Menu, PredefinedMenuItem, Submenu, HELP_SUBMENU_ID, WINDOW_SUBMENU_ID,
    };

    let about_metadata = AboutMetadata {
        name: Some("EnMotion".to_string()),
        version: Some(app.package_info().version.to_string()),
        copyright: app.config().bundle.copyright.clone(),
        ..Default::default()
    };

    let app_menu = Submenu::with_items(
        app,
        "EnMotion",
        true,
        &[
            &PredefinedMenuItem::about(app, Some("关于 EnMotion"), Some(about_metadata))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::services(app, Some("服务"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, Some("隐藏 EnMotion"))?,
            &PredefinedMenuItem::hide_others(app, Some("隐藏其他"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::quit(app, Some("退出 EnMotion"))?,
        ],
    )?;
    let file_menu = Submenu::with_items(
        app,
        "文件",
        true,
        &[&PredefinedMenuItem::close_window(app, Some("关闭窗口"))?],
    )?;
    let edit_menu = Submenu::with_items(
        app,
        "编辑",
        true,
        &[
            &PredefinedMenuItem::undo(app, Some("撤销"))?,
            &PredefinedMenuItem::redo(app, Some("重做"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, Some("剪切"))?,
            &PredefinedMenuItem::copy(app, Some("复制"))?,
            &PredefinedMenuItem::paste(app, Some("粘贴"))?,
            &PredefinedMenuItem::select_all(app, Some("全选"))?,
        ],
    )?;
    let view_menu = Submenu::with_items(
        app,
        "显示",
        true,
        &[&PredefinedMenuItem::fullscreen(app, Some("切换全屏"))?],
    )?;
    let window_menu = Submenu::with_id_and_items(
        app,
        WINDOW_SUBMENU_ID,
        "窗口",
        true,
        &[
            &PredefinedMenuItem::minimize(app, Some("最小化"))?,
            &PredefinedMenuItem::maximize(app, Some("缩放"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, Some("关闭窗口"))?,
        ],
    )?;
    let help_menu = Submenu::with_id(app, HELP_SUBMENU_ID, "帮助", true)?;

    Menu::with_items(
        app,
        &[
            &app_menu,
            &file_menu,
            &edit_menu,
            &view_menu,
            &window_menu,
            &help_menu,
        ],
    )
}

fn main() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            show_main_window(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(SidecarRuntime::default())
        .manage(UpdateRuntime::new(env!("CARGO_PKG_VERSION").to_string()))
        .invoke_handler(tauri::generate_handler![
            updater::desktop_confirm_ui_ready,
            updater::desktop_update_state,
            updater::desktop_check_for_updates,
            updater::desktop_start_update,
            updater::desktop_install_and_restart,
        ]);

    #[cfg(target_os = "macos")]
    let builder = builder.menu(chinese_macos_menu);

    let application = builder
        .setup(|app| {
            sidecar::create_main_window(app)?;
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(error) = sidecar::launch(handle.clone()).await {
                    handle.state::<SidecarRuntime>().stop();
                    eprintln!("EnMotion startup failed: {error}");
                    sidecar::show_startup_error(&handle, &error);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build EnMotion desktop application");

    application.run(|app, event| {
        #[cfg(target_os = "macos")]
        match &event {
            // Closing the last macOS window should keep the application and
            // its local sidecar alive. Hiding instead of destroying the sole
            // webview also guarantees that Dock reopen and second-instance
            // activation can restore the existing authenticated workspace.
            tauri::RunEvent::WindowEvent {
                label,
                event: tauri::WindowEvent::CloseRequested { api, .. },
                ..
            } if label == "main" => {
                api.prevent_close();
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            tauri::RunEvent::Reopen { .. } => show_main_window(app),
            _ => {}
        }

        if matches!(
            event,
            tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
        ) {
            app.state::<SidecarRuntime>().stop();
        }
    });
}

#[cfg(test)]
mod configuration_tests {
    use serde_json::Value;

    fn merge_patch(base: &mut Value, patch: Value) {
        match patch {
            Value::Object(values) => {
                if !base.is_object() {
                    *base = Value::Object(Default::default());
                }
                let target = base.as_object_mut().unwrap();
                for (key, value) in values {
                    if value.is_null() {
                        target.remove(&key);
                    } else {
                        merge_patch(target.entry(key).or_insert(Value::Null), value);
                    }
                }
            }
            value => *base = value,
        }
    }

    fn merged(platform: &str) -> tauri::utils::config::Config {
        let mut base: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let patch: Value = match platform {
            "macos" => serde_json::from_str(include_str!("../tauri.macos.conf.json")).unwrap(),
            "windows" => serde_json::from_str(include_str!("../tauri.windows.conf.json")).unwrap(),
            _ => unreachable!(),
        };
        merge_patch(&mut base, patch);
        serde_json::from_value(base).unwrap()
    }

    #[test]
    fn macos_configuration_deserializes() {
        let config = merged("macos");
        assert!(config.bundle.active);
    }

    #[test]
    fn windows_configuration_deserializes() {
        let config = merged("windows");
        assert!(config.bundle.active);
    }
}
