use std::{
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::sidecar::{
    cancel_update, control_plane_url, create_update_session, prepare_update, SidecarRuntime,
};

pub const UPDATE_STATE_EVENT: &str = "enmotion://update-state";
const HYBRID_SESSION_COOKIE: &str = "enmotion_session";
const UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs(20);
const UPDATE_CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const UPDATE_READ_IDLE_TIMEOUT: Duration = Duration::from_secs(90);
const UPDATE_DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(6 * 60 * 60);

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DownloadProgress {
    downloaded_bytes: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    total_bytes: Option<u64>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateState {
    status: &'static str,
    current_version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    available_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    progress: Option<DownloadProgress>,
    #[serde(skip_serializing_if = "Option::is_none")]
    release_notes: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

struct UpdateInner {
    state: UpdateState,
    update: Option<Update>,
    verified_package: Option<PathBuf>,
    verified_package_sha256: Option<[u8; 32]>,
    operation_running: bool,
}

pub struct UpdateRuntime(Mutex<UpdateInner>);

impl UpdateRuntime {
    pub fn new(current_version: String) -> Self {
        Self(Mutex::new(UpdateInner {
            state: UpdateState {
                status: "idle",
                current_version,
                available_version: None,
                progress: None,
                release_notes: None,
                error: None,
            },
            update: None,
            verified_package: None,
            verified_package_sha256: None,
            operation_running: false,
        }))
    }

    fn snapshot(&self) -> UpdateState {
        self.0
            .lock()
            .map(|inner| inner.state.clone())
            .unwrap_or_else(|_| UpdateState {
                status: "error",
                current_version: "未知".to_string(),
                available_version: None,
                progress: None,
                release_notes: None,
                error: Some("无法读取桌面更新状态".to_string()),
            })
    }
}

fn configure_updater_network(
    builder: tauri_plugin_updater::UpdaterBuilder,
) -> tauri_plugin_updater::UpdaterBuilder {
    builder
        .timeout(UPDATE_CHECK_TIMEOUT)
        .configure_client(|client| {
            client
                .connect_timeout(UPDATE_CONNECT_TIMEOUT)
                .read_timeout(UPDATE_READ_IDLE_TIMEOUT)
        })
}

#[tauri::command]
pub fn desktop_update_state(runtime: tauri::State<'_, UpdateRuntime>) -> UpdateState {
    runtime.snapshot()
}

#[tauri::command]
pub async fn desktop_confirm_ui_ready(app: AppHandle) -> Result<(), String> {
    let task_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || crate::sidecar::commit_update(&task_app))
        .await
        .map_err(|error| user_safe_error("无法确认 EnMotion 界面已就绪", error))?
        .map_err(|error| user_safe_error("无法确认 EnMotion 界面已就绪", error))
}

#[tauri::command]
pub async fn desktop_check_for_updates(app: AppHandle) -> UpdateState {
    let runtime = app.state::<UpdateRuntime>();
    let should_start = runtime
        .0
        .lock()
        .map(|mut inner| {
            if inner.operation_running || matches!(inner.state.status, "downloading" | "installing")
            {
                return false;
            }
            remove_verified_package(&mut inner);
            inner.update = None;
            inner.operation_running = true;
            inner.state.status = "checking";
            inner.state.available_version = None;
            inner.state.release_notes = None;
            inner.state.progress = None;
            inner.state.error = None;
            true
        })
        .unwrap_or(false);
    let state = runtime.snapshot();
    emit(&app, &state);
    if should_start {
        let task_app = app.clone();
        tauri::async_runtime::spawn(async move {
            check_for_updates(task_app).await;
        });
    }
    state
}

#[tauri::command]
pub async fn desktop_start_update(app: AppHandle) -> UpdateState {
    let runtime = app.state::<UpdateRuntime>();
    let selected = runtime.0.lock().ok().and_then(|mut inner| {
        if inner.operation_running {
            return None;
        }
        let update = inner.update.clone()?;
        inner.operation_running = true;
        inner.state.status = "downloading";
        inner.state.error = None;
        inner.state.progress = Some(DownloadProgress {
            downloaded_bytes: 0,
            total_bytes: None,
        });
        Some(update)
    });
    let state = runtime.snapshot();
    emit(&app, &state);
    if let Some(update) = selected {
        let task_app = app.clone();
        tauri::async_runtime::spawn(async move {
            download_update(task_app, update).await;
        });
    }
    state
}

#[tauri::command]
pub async fn desktop_install_and_restart(app: AppHandle) -> UpdateState {
    let runtime = app.state::<UpdateRuntime>();
    let selected = runtime.0.lock().ok().and_then(|mut inner| {
        if inner.operation_running || inner.state.status != "ready" {
            return None;
        }
        let update = inner.update.clone()?;
        let package = inner.verified_package.clone()?;
        let package_sha256 = inner.verified_package_sha256?;
        inner.operation_running = true;
        inner.state.status = "installing";
        inner.state.error = None;
        Some((update, package, package_sha256))
    });
    let state = runtime.snapshot();
    emit(&app, &state);
    if let Some((update, package, package_sha256)) = selected {
        let task_app = app.clone();
        tauri::async_runtime::spawn(async move {
            install_update(task_app, update, package, package_sha256).await;
        });
    }
    state
}

async fn check_for_updates(app: AppHandle) {
    let exit_app = app.clone();
    let result = scoped_updater_async(&app)
        .await
        .and_then(|(builder, control_plane)| {
            configure_updater_network(builder)
                .on_before_exit(move || {
                    exit_app.state::<SidecarRuntime>().stop();
                })
                .build()
                .map(|updater| (updater, control_plane))
                .map_err(|error| error.to_string())
        });
    let result = match result {
        Ok((updater, control_plane)) => match updater.check().await {
            Ok(Some(update)) if !trusted_control_plane_download(&update, &control_plane) => {
                Err("更新服务返回了不受信任的下载地址".to_string())
            }
            Ok(update) => Ok(update),
            Err(error) => Err(error.to_string()),
        },
        Err(error) => Err(error),
    };
    match result {
        Ok(Some(update)) => {
            let state = mutate(&app, |inner| {
                remove_verified_package(inner);
                inner.state.status = "available";
                inner.state.available_version = Some(update.version.clone());
                inner.state.release_notes = update.body.clone();
                inner.state.progress = None;
                inner.state.error = None;
                inner.update = Some(update);
                inner.operation_running = false;
            });
            emit(&app, &state);
        }
        Ok(None) => {
            let state = mutate(&app, |inner| {
                remove_verified_package(inner);
                inner.state.status = "idle";
                inner.state.available_version = None;
                inner.state.release_notes = None;
                inner.state.progress = None;
                inner.state.error = None;
                inner.update = None;
                inner.operation_running = false;
            });
            emit(&app, &state);
        }
        Err(error) => set_error(&app, user_safe_error("检查更新失败", error)),
    }
}

fn scoped_updater(
    app: &AppHandle,
) -> Result<(tauri_plugin_updater::UpdaterBuilder, url::Url), String> {
    let endpoint = app.state::<SidecarRuntime>().endpoint()?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "EnMotion 主窗口不可用".to_string())?;
    let origin: url::Url = format!("{}/", endpoint.origin())
        .parse()
        .map_err(|_| "EnMotion 本地服务地址无效".to_string())?;
    let session_cookie = window
        .cookies_for_url(origin)
        .map_err(|error| format!("无法读取 EnMotion 本地会话：{error}"))?
        .into_iter()
        .find(|cookie| cookie.name() == HYBRID_SESSION_COOKIE)
        .ok_or_else(|| "请先登录 EnMotion，再检查更新".to_string())?;
    let (target, arch) = updater_target_arch();
    let session = create_update_session(
        app,
        target,
        arch,
        &app.package_info().version.to_string(),
        session_cookie.value(),
    )?;
    let update_endpoint: url::Url = session
        .manifest_url
        .parse()
        .map_err(|_| "EnMotion 更新地址无效".to_string())?;
    let control_plane: url::Url = format!("{}/", control_plane_url()?)
        .parse()
        .map_err(|_| "EnMotion 控制服务地址无效".to_string())?;
    if !trusted_control_plane_url(
        &update_endpoint,
        &control_plane,
        "/api/v1/releases/session/",
        "/manifest",
    ) {
        return Err("更新会话返回了不受信任的地址".to_string());
    }
    let builder = app
        .updater_builder()
        .endpoints(vec![update_endpoint])
        .map_err(|error| format!("无法配置专用更新服务：{error}"))?;
    Ok((builder, control_plane))
}

async fn scoped_updater_async(
    app: &AppHandle,
) -> Result<(tauri_plugin_updater::UpdaterBuilder, url::Url), String> {
    let task_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || scoped_updater(&task_app))
        .await
        .map_err(|error| format!("无法创建专用更新会话：{error}"))?
}

fn trusted_control_plane_download(update: &Update, control_plane: &url::Url) -> bool {
    trusted_control_plane_url(
        &update.download_url,
        control_plane,
        "/api/v1/releases/session/",
        "/download",
    )
}

fn trusted_control_plane_url(
    url: &url::Url,
    control_plane: &url::Url,
    path_prefix: &str,
    path_suffix: &str,
) -> bool {
    let capability = url
        .path()
        .strip_prefix(path_prefix)
        .and_then(|path| path.strip_suffix(path_suffix))
        .unwrap_or_default();
    url.scheme() == "https"
        && url.scheme() == control_plane.scheme()
        && url.host_str() == control_plane.host_str()
        && url.port_or_known_default() == control_plane.port_or_known_default()
        && url.username().is_empty()
        && url.password().is_none()
        && url.query().is_none()
        && url.fragment().is_none()
        && (32..=256).contains(&capability.len())
        && capability
            .bytes()
            .all(|value| value.is_ascii_alphanumeric() || matches!(value, b'-' | b'_'))
}

fn updater_target_arch() -> (&'static str, &'static str) {
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    return ("darwin", "aarch64");
    #[cfg(all(target_os = "macos", target_arch = "x86_64"))]
    return ("darwin", "x86_64");
    #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
    return ("windows", "x86_64");
    #[allow(unreachable_code)]
    ("unsupported", "unsupported")
}

async fn download_update(app: AppHandle, update: Update) {
    let announced_version = update.version.clone();
    let update = match refresh_update_session(&app, &announced_version).await {
        Ok(update) => update,
        Err(error) => {
            set_error(&app, user_safe_error("更新授权失败", error));
            return;
        }
    };
    let progress_app = app.clone();
    let mut downloaded = 0_u64;
    let result = update
        .download(
            move |chunk_length, total| {
                downloaded = downloaded.saturating_add(chunk_length as u64);
                let state = mutate(&progress_app, |inner| {
                    inner.state.status = "downloading";
                    inner.state.progress = Some(DownloadProgress {
                        downloaded_bytes: downloaded,
                        total_bytes: total,
                    });
                });
                emit(&progress_app, &state);
            },
            || {},
        )
        .await;
    let bytes = match result {
        Ok(bytes) => bytes,
        Err(error) => {
            set_error(&app, user_safe_error("更新下载或签名验证失败", error));
            return;
        }
    };
    let package_sha256 = sha256_bytes(&bytes);
    let package_path = match verified_package_path(&app, &update.version)
        .and_then(|path| write_private_atomic(&path, &bytes).map(|_| path))
    {
        Ok(path) => path,
        Err(error) => {
            set_error(&app, user_safe_error("无法保存更新包", error));
            return;
        }
    };
    drop(bytes);
    let state = mutate(&app, |inner| {
        remove_verified_package(inner);
        inner.update = Some(update);
        inner.verified_package = Some(package_path);
        inner.verified_package_sha256 = Some(package_sha256);
        inner.state.status = "ready";
        inner.state.progress = None;
        inner.state.error = None;
        inner.operation_running = false;
    });
    emit(&app, &state);
}

async fn refresh_update_session(app: &AppHandle, expected_version: &str) -> Result<Update, String> {
    let exit_app = app.clone();
    let (builder, control_plane) = scoped_updater_async(app).await?;
    let updater = configure_updater_network(builder)
        .on_before_exit(move || {
            exit_app.state::<SidecarRuntime>().stop();
        })
        .build()
        .map_err(|error| error.to_string())?;
    let mut update = updater
        .check()
        .await
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "所选更新已不可用".to_string())?;
    if update.version != expected_version {
        return Err("已有其他版本可用，请重新检查更新".to_string());
    }
    if !trusted_control_plane_download(&update, &control_plane) {
        return Err("更新服务返回了不受信任的下载地址".to_string());
    }
    // The check stays fast, but a signed desktop archive may be large and
    // employees in China may have a slow cross-border route. Preserve an idle
    // read timeout while allowing the total transfer to finish.
    update.timeout = Some(UPDATE_DOWNLOAD_TIMEOUT);
    Ok(update)
}

async fn install_update(
    app: AppHandle,
    update: Update,
    package: PathBuf,
    expected_sha256: [u8; 32],
) {
    let bytes = match fs::read(&package) {
        Ok(bytes) => bytes,
        Err(error) => {
            set_package_error(&app, user_safe_error("已验证的更新包不可用", error));
            return;
        }
    };
    if !constant_time_digest_eq(&sha256_bytes(&bytes), &expected_sha256) {
        set_package_error(&app, "已验证的更新包在安装前发生变化".to_string());
        return;
    }
    if let Err(error) = prepare_update(&app, &update.version) {
        set_error(
            &app,
            user_safe_error("为保护正在进行的任务，更新已暂停", error),
        );
        return;
    }
    if let Err(error) = update.install(&bytes) {
        let _ = cancel_update(&app);
        set_error(&app, user_safe_error("安装更新失败", error));
        return;
    }
    drop(bytes);
    let _ = fs::remove_file(&package);
    app.state::<SidecarRuntime>().stop();
    app.request_restart();
}

fn mutate(app: &AppHandle, operation: impl FnOnce(&mut UpdateInner)) -> UpdateState {
    let runtime = app.state::<UpdateRuntime>();
    let snapshot = match runtime.0.lock() {
        Ok(mut inner) => {
            operation(&mut inner);
            inner.state.clone()
        }
        Err(_) => runtime.snapshot(),
    };
    snapshot
}

fn set_error(app: &AppHandle, error: String) {
    let state = mutate(app, |inner| {
        inner.state.status = "error";
        inner.state.progress = None;
        inner.state.error = Some(error);
        inner.operation_running = false;
    });
    emit(app, &state);
}

fn set_package_error(app: &AppHandle, error: String) {
    let state = mutate(app, |inner| {
        remove_verified_package(inner);
        inner.state.status = "error";
        inner.state.progress = None;
        inner.state.error = Some(error);
        inner.operation_running = false;
    });
    emit(app, &state);
}

fn emit(app: &AppHandle, state: &UpdateState) {
    let _ = app.emit(UPDATE_STATE_EVENT, state);
}

fn remove_verified_package(inner: &mut UpdateInner) {
    inner.verified_package_sha256 = None;
    if let Some(path) = inner.verified_package.take() {
        let _ = fs::remove_file(path);
    }
}

fn sha256_bytes(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}

fn constant_time_digest_eq(left: &[u8; 32], right: &[u8; 32]) -> bool {
    left.iter()
        .zip(right)
        .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

fn verified_package_path(app: &AppHandle, version: &str) -> Result<PathBuf, String> {
    let directory = app
        .path()
        .app_cache_dir()
        .map_err(|error| format!("无法定位 EnMotion 更新缓存：{error}"))?
        .join("updates");
    fs::create_dir_all(&directory)
        .map_err(|error| format!("无法创建 EnMotion 更新缓存：{error}"))?;
    let safe_version: String = version
        .chars()
        .filter(|value| value.is_ascii_alphanumeric() || matches!(value, '.' | '-' | '_'))
        .collect();
    if safe_version.is_empty() {
        return Err("更新版本号不能用作安装包文件名".to_string());
    }
    Ok(directory.join(format!("enmotion-{safe_version}.verified")))
}

fn write_private_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let temporary = path.with_extension("partial");
    fs::write(&temporary, bytes).map_err(|error| format!("无法暂存已验证的更新包：{error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("无法保护已验证的更新包：{error}"))?;
    }
    if path.exists() {
        fs::remove_file(path).map_err(|error| format!("无法替换缓存中的更新包：{error}"))?;
    }
    fs::rename(&temporary, path).map_err(|error| format!("无法发布已验证的更新包：{error}"))
}

fn user_safe_error(prefix: &str, _error: impl std::fmt::Display) -> String {
    // Native/network/library diagnostics may be English and can contain
    // bearer-equivalent capability URLs. Keep those details out of the
    // Chinese interface; release builds can record internal diagnostics in a
    // separately protected log if that becomes necessary.
    format!("{prefix}。请稍后重试；如果问题持续，请联系管理员。")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_contract_uses_expected_status_values() {
        let runtime = UpdateRuntime::new("1.2.3".to_string());
        let state = runtime.snapshot();
        assert_eq!(state.status, "idle");
        assert_eq!(state.current_version, "1.2.3");
    }

    #[test]
    fn cached_update_digest_detects_modification() {
        let expected = sha256_bytes(b"signed updater archive");
        let unchanged = sha256_bytes(b"signed updater archive");
        let modified = sha256_bytes(b"modified updater archive");
        assert!(constant_time_digest_eq(&expected, &unchanged));
        assert!(!constant_time_digest_eq(&expected, &modified));
    }

    #[test]
    fn large_download_timeout_keeps_fast_connect_and_idle_bounds() {
        assert!(UPDATE_DOWNLOAD_TIMEOUT > Duration::from_secs(60 * 60));
        assert!(UPDATE_CHECK_TIMEOUT <= UPDATE_CONNECT_TIMEOUT);
        assert!(UPDATE_READ_IDLE_TIMEOUT < UPDATE_DOWNLOAD_TIMEOUT);
    }

    #[test]
    fn updater_errors_redact_punctuated_capability_urls() {
        let message = user_safe_error(
            "检查更新失败",
            "error sending request for url \
             (https://accounts.enmotion.example/api/v1/releases/session/secret-capability/manifest)",
        );
        assert_eq!(
            message,
            "检查更新失败。请稍后重试；如果问题持续，请联系管理员。"
        );
        assert!(!message.contains("secret-capability"));
        assert!(!message.contains("error sending request"));
    }

    #[test]
    fn updater_capabilities_are_https_and_same_origin_only() {
        let control_plane: url::Url = "https://accounts.enmotion.example/".parse().unwrap();
        let token = "a".repeat(48);
        let manifest: url::Url =
            format!("https://accounts.enmotion.example/api/v1/releases/session/{token}/manifest")
                .parse()
                .unwrap();
        assert!(trusted_control_plane_url(
            &manifest,
            &control_plane,
            "/api/v1/releases/session/",
            "/manifest"
        ));

        for unsafe_url in [
            format!("https://attacker.example/api/v1/releases/session/{token}/manifest"),
            format!("http://accounts.enmotion.example/api/v1/releases/session/{token}/manifest"),
            format!(
                "https://accounts.enmotion.example/api/v1/releases/session/{token}/manifest?token=x"
            ),
            "https://accounts.enmotion.example/api/v1/releases/session/short/manifest".to_string(),
        ] {
            let unsafe_url: url::Url = unsafe_url.parse().unwrap();
            assert!(!trusted_control_plane_url(
                &unsafe_url,
                &control_plane,
                "/api/v1/releases/session/",
                "/manifest"
            ));
        }
    }
}
