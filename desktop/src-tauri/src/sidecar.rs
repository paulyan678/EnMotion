use std::{
    fs::OpenOptions,
    io::{ErrorKind, Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU16, Ordering},
        Mutex,
    },
    time::{Duration, Instant},
};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, Mac};
use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use tauri::{
    path::BaseDirectory, AppHandle, Emitter, Manager, Runtime, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

const RUNTIME_CONFIG_ENV: &str = "ENMOTION_DESKTOP_RUNTIME_CONFIG";
const DEMUCS_WORKER_ENV: &str = "ENMOTION_DEMUCS_WORKER";
const NONCE_HEADER: &str = "X-EnMotion-Desktop-Nonce";
const HYBRID_SESSION_COOKIE: &str = "enmotion_session";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(120);
const HTTP_TIMEOUT: Duration = Duration::from_secs(4);
const OUTPUT_ACCESS_TIMEOUT: Duration = Duration::from_secs(30);
const OUTPUT_ACCESS_DENIED: &str = "enmotion-output-access-denied";
const OUTPUT_ACCESS_FAILED: &str = "enmotion-output-access-failed";
const OUTPUT_ACCESS_TIMED_OUT: &str = "enmotion-output-access-timed-out";
const GENERIC_RUNTIME_ERROR_MESSAGE: &str =
    "EnMotion 无法启动本地服务。请重新启动应用；如果问题持续，请联系管理员。";
#[cfg(target_os = "macos")]
const OUTPUT_ACCESS_FAILED_MESSAGE: &str =
    "EnMotion 无法准备本地输出文件夹。请确认磁盘可用且 EnMotion 应用数据目录可读写，然后重新启动应用。";
#[cfg(target_os = "windows")]
const OUTPUT_ACCESS_FAILED_MESSAGE: &str =
    "EnMotion 无法准备本地输出文件夹。请确认磁盘可用且 EnMotion 应用数据目录可读写，然后重新启动应用。";
#[cfg(target_os = "macos")]
const OUTPUT_ACCESS_PERMISSION_MESSAGE: &str =
    "EnMotion 无法访问本地应用数据目录。请检查该目录的所有者和读写权限，然后重新启动应用。";
#[cfg(target_os = "windows")]
const OUTPUT_ACCESS_PERMISSION_MESSAGE: &str =
    "EnMotion 无法访问本地应用数据目录。请检查该目录的所有者和读写权限，然后重新启动应用。";
#[cfg(target_os = "macos")]
const OUTPUT_ACCESS_TIMED_OUT_MESSAGE: &str = OUTPUT_ACCESS_PERMISSION_MESSAGE;
#[cfg(target_os = "windows")]
const OUTPUT_ACCESS_TIMED_OUT_MESSAGE: &str =
    "EnMotion 无法在 30 秒内确认本地输出文件夹可用。请检查磁盘状态和应用数据目录，然后重新启动应用。";

#[derive(Clone, Debug)]
pub struct Endpoint {
    pub port: u16,
    pub nonce: String,
}

impl Endpoint {
    pub fn origin(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }
}

pub struct SidecarRuntime {
    child: Mutex<Option<CommandChild>>,
    endpoint: Mutex<Option<Endpoint>>,
    expected_exit: AtomicBool,
    pub allowed_port: AtomicU16,
}

impl Default for SidecarRuntime {
    fn default() -> Self {
        Self {
            child: Mutex::new(None),
            endpoint: Mutex::new(None),
            expected_exit: AtomicBool::new(false),
            allowed_port: AtomicU16::new(0),
        }
    }
}

impl SidecarRuntime {
    pub fn endpoint(&self) -> Result<Endpoint, String> {
        self.endpoint
            .lock()
            .map_err(|_| "sidecar endpoint lock is poisoned".to_string())?
            .clone()
            .ok_or_else(|| "EnMotion sidecar is not ready".to_string())
    }

    pub fn stop(&self) {
        self.expected_exit.store(true, Ordering::Release);
        self.allowed_port.store(0, Ordering::Release);
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.take() {
                terminate_sidecar(child);
            }
        }
    }

    fn install(&self, endpoint: Endpoint, child: CommandChild) -> Result<(), String> {
        *self
            .endpoint
            .lock()
            .map_err(|_| "sidecar endpoint lock is poisoned".to_string())? = Some(endpoint.clone());
        *self
            .child
            .lock()
            .map_err(|_| "sidecar child lock is poisoned".to_string())? = Some(child);
        self.allowed_port.store(endpoint.port, Ordering::Release);
        Ok(())
    }

    fn note_termination(&self, pid: u32) -> bool {
        self.allowed_port.store(0, Ordering::Release);
        if let Ok(mut guard) = self.child.lock() {
            if guard.as_ref().is_some_and(|child| child.pid() == pid) {
                guard.take();
            }
        }
        !self.expected_exit.load(Ordering::Acquire)
    }
}

fn terminate_sidecar(child: CommandChild) {
    #[cfg(unix)]
    {
        // Give the packaged Python runtime a graceful shutdown opportunity
        // before using the hard-kill fallback.
        let pid = child.pid() as libc::pid_t;
        if unsafe { libc::kill(pid, libc::SIGTERM) } == 0 {
            std::thread::sleep(Duration::from_millis(750));
        }
    }
    let _ = child.kill();
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeContract {
    schema_version: u8,
    host: &'static str,
    port: u16,
    nonce: String,
    static_dir: String,
    data_dir: String,
    output_dir: String,
    current_version: String,
    control_plane_url: String,
}

#[derive(Deserialize)]
struct ReadyResponse {
    ready: bool,
    version: String,
    proof: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateSession {
    pub manifest_url: String,
}

pub fn create_main_window<R: Runtime>(
    app: &tauri::App<R>,
) -> Result<tauri::WebviewWindow<R>, tauri::Error> {
    let handle = app.handle().clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("EnMotion")
        .inner_size(1360.0, 860.0)
        .min_inner_size(1040.0, 700.0)
        .center()
        // The bundled bootstrap is intentionally safe to show before the
        // loopback service is ready. Keeping this window hidden made the
        // PyInstaller startup time look like an application hang.
        .visible(true)
        .devtools(cfg!(debug_assertions))
        .on_navigation(move |url| {
            let bundled_page = (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
                || (matches!(url.scheme(), "http" | "https")
                    && url.host_str() == Some("tauri.localhost"));
            if bundled_page {
                return true;
            }
            if url.scheme() != "http" || url.host_str() != Some("127.0.0.1") {
                return false;
            }
            let allowed_port = handle
                .state::<SidecarRuntime>()
                .allowed_port
                .load(Ordering::Acquire);
            allowed_port != 0 && url.port_or_known_default() == Some(allowed_port)
        })
        .build()
}

pub async fn launch<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    let startup_started = Instant::now();
    report_startup_phase(
        &app,
        "runtime-contract",
        "正在准备安全的本地工作区…",
        startup_started,
    );
    let port = reserve_loopback_port()?;
    let nonce = random_nonce();
    let static_dir = resolve_static_dir(&app)?;
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("cannot resolve EnMotion data directory: {error}"))?;
    let output_dir = data_dir.join("enmotion-output");
    std::fs::create_dir_all(&data_dir)
        .map_err(|error| format!("cannot create EnMotion data directory: {error}"))?;
    report_startup_phase(
        &app,
        "output-access",
        output_access_status_message(),
        startup_started,
    );
    verify_output_directory_access(output_dir.clone()).await?;

    let endpoint = Endpoint { port, nonce };
    let control_plane_url = control_plane_url()?;
    let contract = RuntimeContract {
        schema_version: 1,
        host: "127.0.0.1",
        port,
        nonce: endpoint.nonce.clone(),
        static_dir: path_string(static_dir)?,
        data_dir: path_string(data_dir.clone())?,
        output_dir: path_string(output_dir)?,
        current_version: app.package_info().version.to_string(),
        control_plane_url,
    };
    let encoded = URL_SAFE_NO_PAD.encode(
        serde_json::to_vec(&contract)
            .map_err(|error| format!("cannot serialize sidecar runtime contract: {error}"))?,
    );
    let sidecar_executable = resolve_core_sidecar(&app)?;
    let demucs_worker = resolve_demucs_worker()?;

    report_startup_phase(
        &app,
        "sidecar-spawn",
        "正在启动本地创作服务…",
        startup_started,
    );
    let (mut events, child) = app
        .shell()
        .command(sidecar_executable)
        .arg("--desktop-runtime")
        .current_dir(&data_dir)
        .env(RUNTIME_CONFIG_ENV, encoded)
        .env(DEMUCS_WORKER_ENV, demucs_worker)
        .spawn()
        .map_err(|error| format!("cannot start EnMotion sidecar: {error}"))?;
    let pid = child.pid();
    app.state::<SidecarRuntime>()
        .install(endpoint.clone(), child)?;

    let monitor_app = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if matches!(event, CommandEvent::Terminated(_)) {
                if monitor_app.state::<SidecarRuntime>().note_termination(pid) {
                    let _ = monitor_app
                        .emit("enmotion://runtime-failed", "EnMotion 本地服务意外停止。");
                    show_runtime_error(&monitor_app);
                }
                break;
            }
        }
    });

    wait_until_ready(&endpoint, app.package_info().version.to_string()).await?;
    report_startup_phase(
        &app,
        "sidecar-ready",
        "本地服务已就绪，正在打开工作区…",
        startup_started,
    );
    let bootstrap_url = format!(
        "{}/_desktop/bootstrap/{}",
        endpoint.origin(),
        endpoint.nonce
    )
    .parse()
    .map_err(|error| format!("invalid EnMotion bootstrap URL: {error}"))?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "EnMotion main window was not created".to_string())?;
    window
        .navigate(bootstrap_url)
        .map_err(|error| format!("cannot open EnMotion: {error}"))?;
    window
        .show()
        .map_err(|error| format!("cannot show EnMotion window: {error}"))?;
    eprintln!(
        "[startup] phase=application-navigated elapsed_ms={}",
        startup_started.elapsed().as_millis()
    );
    Ok(())
}

fn report_startup_phase<R: Runtime>(
    app: &AppHandle<R>,
    phase: &str,
    message: &str,
    started: Instant,
) {
    eprintln!(
        "[startup] phase={phase} elapsed_ms={}",
        started.elapsed().as_millis()
    );
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    if let Ok(serialized) = serde_json::to_string(message) {
        let _ = window.eval(format!("window.enmotionDesktopBootStatus?.({serialized})"));
    }
}

pub fn commit_update<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let endpoint = app.state::<SidecarRuntime>().endpoint()?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "EnMotion main window is unavailable".to_string())?;
    let origin: url::Url = format!("{}/", endpoint.origin())
        .parse()
        .map_err(|_| "EnMotion sidecar origin is invalid".to_string())?;
    let session_cookie = window
        .cookies_for_url(origin)
        .map_err(|error| format!("Cannot read the local EnMotion session: {error}"))?
        .into_iter()
        .find(|cookie| cookie.name() == HYBRID_SESSION_COOKIE)
        .ok_or_else(|| "请先登录 EnMotion，再确认更新已就绪".to_string())?;
    request(
        &endpoint,
        "POST",
        "/_desktop/commit-update",
        "{}",
        HTTP_TIMEOUT,
        Some(session_cookie.value()),
    )
    .map(|_| ())
}

pub fn prepare_update<R: Runtime>(app: &AppHandle<R>, target_version: &str) -> Result<(), String> {
    let endpoint = app.state::<SidecarRuntime>().endpoint()?;
    let body = serde_json::json!({ "targetVersion": target_version }).to_string();
    post_json(
        &endpoint,
        "/_desktop/prepare-update",
        &body,
        Duration::from_secs(30),
    )
    .map(|_| ())
}

pub fn cancel_update<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let endpoint = app.state::<SidecarRuntime>().endpoint()?;
    post_json(&endpoint, "/_desktop/cancel-update", "{}", HTTP_TIMEOUT).map(|_| ())
}

pub fn create_update_session<R: Runtime>(
    app: &AppHandle<R>,
    target: &str,
    arch: &str,
    current_version: &str,
    session_cookie: &str,
) -> Result<UpdateSession, String> {
    if session_cookie.is_empty()
        || session_cookie
            .bytes()
            .any(|value| !value.is_ascii_alphanumeric() && !matches!(value, b'-' | b'_'))
    {
        return Err("The local employee session is invalid".to_string());
    }
    let endpoint = app.state::<SidecarRuntime>().endpoint()?;
    let body = serde_json::json!({
        "target": target,
        "arch": arch,
        "currentVersion": current_version,
    })
    .to_string();
    let response = request(
        &endpoint,
        "POST",
        "/_desktop/updater/session",
        &body,
        Duration::from_secs(30),
        Some(session_cookie),
    )?;
    serde_json::from_str(&response)
        .map_err(|_| "EnMotion sidecar returned an invalid update session".to_string())
}

pub fn show_startup_error<R: Runtime>(app: &AppHandle<R>, error: &str) {
    show_runtime_error_message(app, startup_error_message(error));
}

pub fn show_runtime_error<R: Runtime>(app: &AppHandle<R>) {
    show_runtime_error_message(app, GENERIC_RUNTIME_ERROR_MESSAGE);
}

fn show_runtime_error_message<R: Runtime>(app: &AppHandle<R>, message: &'static str) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let bundled_url = if cfg!(target_os = "windows") {
        "http://tauri.localhost/index.html"
    } else {
        "tauri://localhost/index.html"
    };
    if let Ok(url) = bundled_url.parse() {
        let _ = window.navigate(url);
    }
    let _ = window.show();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(250)).await;
        let serialized = serde_json::to_string(message)
            .unwrap_or_else(|_| "\"EnMotion 无法启动。\"".to_string());
        let _ = window.eval(format!("window.enmotionDesktopBootError?.({serialized})"));
    });
}

fn startup_error_message(error: &str) -> &'static str {
    if error.starts_with(OUTPUT_ACCESS_DENIED) {
        OUTPUT_ACCESS_PERMISSION_MESSAGE
    } else if error.starts_with(OUTPUT_ACCESS_TIMED_OUT) {
        OUTPUT_ACCESS_TIMED_OUT_MESSAGE
    } else if error.starts_with(OUTPUT_ACCESS_FAILED) {
        OUTPUT_ACCESS_FAILED_MESSAGE
    } else {
        GENERIC_RUNTIME_ERROR_MESSAGE
    }
}

#[cfg(target_os = "macos")]
fn output_access_status_message() -> &'static str {
    "正在确认本地输出文件夹…"
}

#[cfg(target_os = "windows")]
fn output_access_status_message() -> &'static str {
    "正在确认本地输出文件夹…"
}

fn output_access_error(operation: &str, error: std::io::Error) -> String {
    let category = if error.kind() == ErrorKind::PermissionDenied {
        OUTPUT_ACCESS_DENIED
    } else {
        OUTPUT_ACCESS_FAILED
    };
    format!("{category}: {operation}: {error}")
}

struct OutputAccessProbe(PathBuf);

impl Drop for OutputAccessProbe {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

async fn verify_output_directory_access(output_dir: PathBuf) -> Result<(), String> {
    let task = tauri::async_runtime::spawn_blocking(move || {
        verify_output_directory_access_blocking(&output_dir)
    });
    match tokio::time::timeout(OUTPUT_ACCESS_TIMEOUT, task).await {
        Ok(Ok(result)) => result,
        Ok(Err(error)) => Err(format!(
            "{OUTPUT_ACCESS_FAILED}: access task failed: {error}"
        )),
        Err(_) => Err(format!(
            "{OUTPUT_ACCESS_TIMED_OUT}: no permission decision within {} seconds",
            OUTPUT_ACCESS_TIMEOUT.as_secs()
        )),
    }
}

fn verify_output_directory_access_blocking(output_dir: &Path) -> Result<(), String> {
    std::fs::create_dir_all(output_dir)
        .map_err(|error| output_access_error("create output directory", error))?;

    // Enumerate existing contents before writing so startup fails clearly if
    // the app-owned storage is unavailable or was assigned unsafe ownership.
    let mut entries = std::fs::read_dir(output_dir)
        .map_err(|error| output_access_error("read existing output directory", error))?;
    if let Some(entry) = entries.next() {
        entry.map_err(|error| output_access_error("read existing output entry", error))?;
    }

    // Also verify that new output can be created. The random probe is removed
    // immediately, with a drop guard as a best-effort cleanup if removal fails.
    let mut suffix = [0_u8; 8];
    OsRng.fill_bytes(&mut suffix);
    let probe = output_dir.join(format!(".enmotion-access-{}.tmp", hex::encode(suffix)));
    let handle = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&probe)
        .map_err(|error| output_access_error("create output probe", error))?;
    let _cleanup = OutputAccessProbe(probe.clone());
    drop(handle);
    std::fs::remove_file(&probe)
        .map_err(|error| output_access_error("remove output probe", error))?;
    Ok(())
}

fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("cannot reserve a loopback port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("cannot inspect reserved loopback port: {error}"))
}

fn random_nonce() -> String {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    hex::encode(bytes)
}

fn resolve_static_dir<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let path = app
        .path()
        .resolve("web/static", BaseDirectory::Resource)
        .map_err(|error| format!("cannot resolve staged frontend: {error}"))?;
    if !path.join("index.html").is_file() {
        return Err(format!(
            "packaged frontend is missing: {}",
            path.join("index.html").display()
        ));
    }
    Ok(path)
}

fn resolve_core_sidecar<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let name = if cfg!(target_os = "windows") {
        "sidecar/enmotion-sidecar.exe"
    } else {
        "sidecar/enmotion-sidecar"
    };
    let path = app
        .path()
        .resolve(name, BaseDirectory::Resource)
        .map_err(|error| format!("cannot resolve packaged EnMotion runtime: {error}"))?;
    if !path.is_file() {
        return Err(format!(
            "packaged EnMotion runtime is missing: {}",
            path.display()
        ));
    }
    Ok(path)
}

fn resolve_demucs_worker() -> Result<PathBuf, String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("cannot resolve EnMotion executable: {error}"))?;
    let directory = executable
        .parent()
        .ok_or_else(|| "cannot resolve EnMotion executable directory".to_string())?;
    let name = if cfg!(target_os = "windows") {
        "enmotion-demucs-worker.exe"
    } else {
        "enmotion-demucs-worker"
    };
    let path = directory.join(name);
    if !path.is_file() {
        return Err(format!(
            "packaged Demucs worker is missing: {}",
            path.display()
        ));
    }
    Ok(path)
}

fn path_string(path: PathBuf) -> Result<String, String> {
    path.into_os_string()
        .into_string()
        .map_err(|_| "EnMotion runtime paths must be valid Unicode".to_string())
}

pub fn control_plane_url() -> Result<String, String> {
    #[cfg(debug_assertions)]
    let runtime_override = std::env::var("ENMOTION_CONTROL_PLANE_URL").ok();
    #[cfg(not(debug_assertions))]
    let runtime_override: Option<String> = None;
    let configured = runtime_override
        .or_else(|| option_env!("ENMOTION_CONTROL_PLANE_URL").map(str::to_string))
        .unwrap_or_else(|| "https://api.enmotion.example.invalid".to_string());
    let normalized = configured.trim().trim_end_matches('/').to_string();
    let parsed = url::Url::parse(&normalized)
        .map_err(|_| "EnMotion control-plane URL is not an absolute URL".to_string())?;
    let host = parsed
        .host_str()
        .ok_or_else(|| "EnMotion control-plane URL has no host".to_string())?;
    let loopback = matches!(host, "localhost" | "127.0.0.1" | "::1");
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && loopback) {
        return Err("EnMotion control-plane URL must use HTTPS".to_string());
    }
    if !matches!(parsed.path(), "" | "/")
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return Err("EnMotion control-plane URL must be an origin without credentials".to_string());
    }
    Ok(normalized)
}

async fn wait_until_ready(endpoint: &Endpoint, version: String) -> Result<(), String> {
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    let expected_proof = readiness_proof(endpoint, &version)?;
    loop {
        if let Ok(body) = get(endpoint, "/_desktop/ready", HTTP_TIMEOUT) {
            if let Ok(response) = serde_json::from_str::<ReadyResponse>(&body) {
                if response.ready
                    && response.version == version
                    && constant_time_eq(response.proof.as_bytes(), expected_proof.as_bytes())
                {
                    return Ok(());
                }
            }
        }
        if Instant::now() >= deadline {
            return Err("EnMotion sidecar did not become ready within 120 seconds".to_string());
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
}

fn readiness_proof(endpoint: &Endpoint, version: &str) -> Result<String, String> {
    let key =
        hex::decode(&endpoint.nonce).map_err(|_| "desktop nonce is not valid hex".to_string())?;
    let mut mac =
        Hmac::<Sha256>::new_from_slice(&key).map_err(|_| "cannot initialize HMAC".to_string())?;
    mac.update(format!("enmotion-desktop-ready-v1:{version}:{}", endpoint.port).as_bytes());
    Ok(hex::encode(mac.finalize().into_bytes()))
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .zip(right)
            .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
            == 0
}

fn get(endpoint: &Endpoint, path: &str, timeout: Duration) -> Result<String, String> {
    request(endpoint, "GET", path, "", timeout, None)
}

fn post_json(
    endpoint: &Endpoint,
    path: &str,
    body: &str,
    timeout: Duration,
) -> Result<String, String> {
    request(endpoint, "POST", path, body, timeout, None)
}

fn request(
    endpoint: &Endpoint,
    method: &str,
    path: &str,
    body: &str,
    timeout: Duration,
    session_cookie: Option<&str>,
) -> Result<String, String> {
    let mut stream = TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", endpoint.port)
            .parse()
            .map_err(|_| "invalid loopback endpoint".to_string())?,
        timeout,
    )
    .map_err(|error| format!("EnMotion sidecar is unavailable: {error}"))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|error| format!("cannot set sidecar timeout: {error}"))?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|error| format!("cannot set sidecar timeout: {error}"))?;
    let cookie_header = session_cookie
        .map(|value| format!("Cookie: {HYBRID_SESSION_COOKIE}={value}\r\n"))
        .unwrap_or_default();
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n{}: {}\r\n{}Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        endpoint.port,
        NONCE_HEADER,
        endpoint.nonce,
        cookie_header,
        body.len(),
        body,
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("cannot contact EnMotion sidecar: {error}"))?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("cannot read EnMotion sidecar response: {error}"))?;
    let (head, response_body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| "EnMotion sidecar returned an invalid HTTP response".to_string())?;
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| "EnMotion sidecar returned an invalid HTTP status".to_string())?;
    if !(200..300).contains(&status) {
        return Err(format!(
            "EnMotion sidecar rejected {path} with HTTP {status}: {}",
            response_body.chars().take(500).collect::<String>()
        ));
    }
    Ok(response_body.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nonce_is_256_bit_lowercase_hex() {
        let nonce = random_nonce();
        assert_eq!(nonce.len(), 64);
        assert!(nonce
            .chars()
            .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase()));
    }

    #[test]
    fn navigation_proof_depends_on_version_and_port() {
        let endpoint = Endpoint {
            port: 49152,
            nonce: "00".repeat(32),
        };
        let first = readiness_proof(&endpoint, "1.0.0").unwrap();
        let second = readiness_proof(&endpoint, "1.0.1").unwrap();
        assert_eq!(first.len(), 64);
        assert_ne!(first, second);
    }

    #[test]
    fn control_plane_url_defaults_to_a_secure_origin() {
        let value = control_plane_url().unwrap();
        assert!(value.starts_with("https://"));
        assert!(!value.ends_with('/'));
    }

    #[test]
    fn output_access_probe_is_removed_after_success() {
        let root = std::env::temp_dir().join(format!("enmotion-output-probe-{}", random_nonce()));
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("existing-workspace.json"), b"{}").unwrap();

        verify_output_directory_access_blocking(&root).unwrap();

        let remaining = std::fs::read_dir(&root)
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect::<Vec<_>>();
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0], "existing-workspace.json");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn output_access_errors_have_actionable_copy() {
        assert_eq!(
            startup_error_message("enmotion-output-access-denied: permission denied"),
            OUTPUT_ACCESS_PERMISSION_MESSAGE,
        );
        assert_eq!(
            startup_error_message("enmotion-output-access-timed-out: pending prompt"),
            OUTPUT_ACCESS_TIMED_OUT_MESSAGE,
        );
        assert_eq!(
            startup_error_message("enmotion-output-access-failed: disk full"),
            OUTPUT_ACCESS_FAILED_MESSAGE,
        );
        assert_eq!(
            startup_error_message("sidecar exited"),
            GENERIC_RUNTIME_ERROR_MESSAGE,
        );
    }

    #[test]
    fn output_access_error_preserves_failure_category() {
        assert!(
            output_access_error("probe", std::io::Error::from(ErrorKind::PermissionDenied))
                .starts_with(OUTPUT_ACCESS_DENIED)
        );
        assert!(
            output_access_error("probe", std::io::Error::from(ErrorKind::Other))
                .starts_with(OUTPUT_ACCESS_FAILED)
        );
    }
}
