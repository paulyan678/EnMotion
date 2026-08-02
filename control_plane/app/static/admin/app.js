"use strict";

function readCookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

const state = {
  csrf: readCookie("enmotion_admin_csrf") || sessionStorage.getItem("enmotion-admin-csrf") || "",
  authGeneration: 0,
  users: [],
  providerConfig: null,
};
let refreshPromise = null;
const $ = (selector) => document.querySelector(selector);
const notice = $("#notice");
const MODEL_CATALOG = Object.freeze({
  "chat.completions": [
    ["deepseek-v4-flash", "DeepSeek V4 Flash"],
    ["qwen3.7-max", "Qwen 3.7 Max"],
    ["deepseek-v4-pro", "DeepSeek V4 Pro"],
  ],
  "images.generations": [["gpt-image-2", "GPT Image 2"]],
  "images.edits": [["gpt-image-2", "GPT Image 2"]],
  "video.generations": [
    ["doubao-seedance-2-0-260128", "Seedance 2.0"],
    ["doubao-seedance-2-0-fast-260128", "Seedance 2.0 Fast"],
    ["doubao-seedance-2-0-mini-260615", "Seedance 2.0 Mini"],
  ],
});
const PROVIDER_MODELS = Object.freeze([
  ["deepseek-v4-flash", "DeepSeek V4 Flash", "文本"],
  ["qwen3.7-max", "Qwen 3.7 Max", "文本"],
  ["deepseek-v4-pro", "DeepSeek V4 Pro", "文本"],
  ["gpt-image-2", "GPT Image 2", "图像"],
  ["doubao-seedance-2-0-260128", "Seedance 2.0", "视频"],
  ["doubao-seedance-2-0-fast-260128", "Seedance 2.0 Fast", "视频"],
  ["doubao-seedance-2-0-mini-260615", "Seedance 2.0 Mini", "视频"],
]);
const ROLE_LABELS = Object.freeze({
  admin: "管理员",
  user: "普通用户",
});
const OPERATION_LABELS = Object.freeze({
  "chat.completions": "文本对话",
  "images.generations": "图像生成",
  "images.edits": "图像编辑",
  "video.generations": "视频生成",
});
const LEDGER_TYPE_LABELS = Object.freeze({
  adjustment: "额度调整",
  reserve: "预留额度",
  settle: "确认扣款",
  refund: "退还额度",
});
const ERROR_CODE_LABELS = Object.freeze({
  control_plane_restart: "控制服务重启后等待核对",
  provider_connect_failed: "无法连接服务提供商",
  provider_rejected: "服务提供商拒绝了请求",
  invalid_video_submission_response: "视频服务返回了无效结果",
  ambiguous_provider_transport_error: "服务提供商传输结果不明确，等待核对",
  ambiguous_provider_server_error: "服务提供商响应不明确，等待核对",
  ambiguous_provider_redirect: "服务提供商返回意外跳转，等待核对",
  ambiguous_video_submission_response_error: "视频任务提交结果不明确，等待核对",
  settlement_persistence_failed: "扣款记录保存失败，等待核对",
  interrupted_before_terminal_state: "请求在完成前中断，等待核对",
});
const AUDIT_ACTION_LABELS = Object.freeze({
  "system.admin_bootstrapped": "创建初始管理员",
  "admin.user_created": "创建账号",
  "admin.user_status_changed": "更改账号状态",
  "admin.password_reset": "重置账号密码",
  "admin.sessions_revoked": "撤销账号会话",
  "admin.credit_adjusted": "调整账号额度",
  "admin.credit_adjustment_replayed": "重复提交额度调整",
  "admin.rate_card_created": "创建计费规则",
  "admin.rate_card_updated": "更新计费规则",
  "admin.rate_card_deleted": "删除计费规则",
  "admin.provider_config_updated": "更新共享 API 配置",
  "admin.usage_settled": "确认用量扣款",
  "admin.usage_settlement_replayed": "重复确认用量扣款",
  "admin.usage_refunded": "退还用量额度",
  "admin.usage_refund_replayed": "重复退还用量额度",
  "auth.login_failed": "登录失败",
  "auth.login": "登录成功",
  "auth.refresh_reuse_detected": "检测到登录凭证重复使用",
  "auth.refresh_rotated": "更新登录凭证",
  "auth.logout": "退出登录",
  "auth.password_changed": "修改密码",
  "release.session_created": "创建更新下载授权",
});
const AUDIT_TARGET_LABELS = Object.freeze({
  user: "账号",
  session: "登录会话",
  rate_card: "计费规则",
  usage_request: "用量请求",
  release_grant: "更新下载授权",
  provider_configuration: "共享 API 配置",
});
const AUDIT_DETAIL_KEY_LABELS = Object.freeze({
  username: "用户名",
  device_label: "设备名称",
  delta: "调整数额",
  ledger_entry_id: "额度明细编号",
  idempotency_key: "唯一提交标识",
  replay: "是否重复提交",
  operation: "操作类型",
  model: "AI 模型",
  unit_cost: "消耗额度",
  selectors: "筛选条件",
  changed_fields: "更改字段",
  changed_models: "更改模型",
  version: "配置版本",
  base_url: "服务商基础地址",
  charged_units: "扣除额度",
  reserved_units: "预留额度",
  target: "发布目标",
  arch: "处理器架构",
  platform: "系统平台",
  release_version: "发布版本",
  active: "是否启用",
  role: "角色",
  sessions_revoked: "已撤销会话数",
  count: "数量",
  except_session_id: "保留的会话编号",
  initial_credits: "初始额度",
});
const SYSTEM_REASON_LABELS = Object.freeze({
  "initial account credit": "账号初始额度",
  account_deactivated: "账号已停用",
  admin_password_reset: "管理员重置密码",
  admin_revoked: "管理员撤销会话",
  password_changed: "密码已修改",
  refresh_token_reuse: "登录凭证被重复使用",
  "provider accepted request": "服务提供商已接受请求",
  "provider connection failed before acceptance": "服务提供商接收请求前连接失败",
  "provider rejected request": "服务提供商拒绝请求",
});
const RELEASE_TARGET_LABELS = Object.freeze({
  "aarch64-apple-darwin": "macOS（Apple 芯片）",
  "x86_64-apple-darwin": "macOS（英特尔）",
  "x86_64-pc-windows-msvc": "Windows（64 位）",
});
const ARCH_LABELS = Object.freeze({
  aarch64: "Apple 芯片",
  x86_64: "64 位处理器",
});
const PLATFORM_LABELS = Object.freeze({
  darwin: "macOS",
  windows: "Windows",
});
const API_DETAIL_TRANSLATIONS = Object.freeze({
  "authentication required": "请先登录",
  "invalid session": "登录会话无效",
  "session expired or revoked": "登录会话已过期或被撤销",
  "session unavailable": "登录会话不可用",
  "CSRF validation failed": "安全校验失败，请刷新页面后重试",
  "administrator role required": "需要管理员权限",
  "too many login attempts": "登录尝试次数过多，请稍后重试",
  "password verification capacity is busy; retry shortly": "密码验证繁忙，请稍后重试",
  "password hashing capacity is busy; retry shortly": "密码处理繁忙，请稍后重试",
  "invalid username or password": "用户名或密码错误",
  "invalid or expired refresh token": "登录凭证无效或已过期",
  "username already exists": "用户名已存在",
  "cannot deactivate your own account": "不能停用当前登录的账号",
  "user not found": "未找到该用户",
  "usage request not found": "未找到该用量记录",
  "rate card not found": "未找到该计费规则",
  "unsupported usage status": "不支持该用量状态",
  "model capability does not match the operation": "所选 AI 模型不支持该操作类型",
  "credit balances cannot be negative": "额度余额不能为负数",
  "credit adjustment key was reused with different values": "本次额度调整的唯一标识已被其他内容使用",
  "adjustment would make the available balance negative": "调整后可用额度不能为负数",
  "refunded usage cannot be settled": "已退款的用量不能再确认扣款",
  "settled usage cannot be refunded": "已确认扣款的用量不能再退款",
  "charged units must fit inside the reservation": "实际扣除额度不能超过预留额度",
  "reserved balance invariant failed": "预留额度校验失败，请联系技术人员",
  "username must be 3-64 characters using letters, numbers, '.', '_' or '-'":
    "用户名须为 3 至 64 个字符，仅可使用英文字母、数字、点、下划线或连字符",
  "password must contain at least 6 characters": "密码至少需要 6 个字符",
  "password must contain at least 12 characters": "密码至少需要 12 个字符",
  "password must not exceed 256 characters": "密码不能超过 256 个字符",
  "password must not be only whitespace": "密码不能只包含空格",
  "unsupported provider model": "不支持所选 AI 模型",
  "provider credential is empty or invalid": "API 密钥为空或格式无效",
  "provider configuration did not change": "没有需要保存的 API 配置更改",
  "provider validation failed: endpoint or TLS unavailable":
    "无法连接服务商，或服务商的 TLS 证书无效。配置未保存。",
  "provider validation failed: timed out": "服务商验证超时。配置未保存，请稍后重试。",
  "provider validation failed: credentials rejected": "服务商拒绝了 API 密钥。配置未保存。",
  "provider validation failed: service temporarily unavailable":
    "服务商暂时不可用。配置未保存，请稍后重试。",
  "provider validation failed: service rejected the request":
    "服务商拒绝了配置验证请求。配置未保存。",
  "provider validation failed: models endpoint is incompatible":
    "服务商的模型列表接口不兼容。配置未保存。",
  "provider validation failed: configured model unavailable":
    "API 密钥无法访问已配置的模型。配置未保存。",
  "provider validation failed: response too large":
    "服务商返回的模型列表异常过大。配置未保存。",
  "provider base URL must use HTTPS": "服务商基础地址必须使用 HTTPS",
  "provider base URL must include a hostname": "服务商基础地址必须包含主机名",
  "provider base URL must not contain credentials, parameters, a query, or a fragment":
    "服务商基础地址不能包含账号、密钥、参数、查询内容或片段",
  "set ENMOTION_PROVIDER_CONFIG_MASTER_KEY before saving provider credentials":
    "服务器尚未设置 API 配置加密主密钥，请先联系运维人员完成安全配置",
});
const FIELD_LABELS = Object.freeze({
  username: "用户名",
  password: "密码",
  new_password: "新密码",
  role: "角色",
  initial_credits: "初始额度",
  operation: "操作类型",
  model: "AI 模型",
  unit_cost: "消耗额度",
  priority: "优先级",
  selectors: "筛选条件",
  amount: "调整数额",
  delta: "调整数额",
  reason: "原因",
  active: "是否启用",
  base_url: "服务商基础地址",
  credentials: "API 密钥",
});

function showNotice(message, error = false) {
  notice.textContent = message;
  notice.classList.remove("hidden", "error");
  if (error) notice.classList.add("error");
  window.setTimeout(() => notice.classList.add("hidden"), 5000);
}

function beginFormSubmit(form) {
  if (form.dataset.busy === "true") return false;
  form.dataset.busy = "true";
  form.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  return true;
}

function endFormSubmit(form) {
  form.dataset.busy = "false";
  form.querySelectorAll("button").forEach((button) => { button.disabled = false; });
}

function translateApiText(message) {
  if (typeof message !== "string") return message;
  if (API_DETAIL_TRANSLATIONS[message]) return API_DETAIL_TRANSLATIONS[message];
  if (message.startsWith("unsupported rate selectors:")) {
    return `不支持以下筛选条件：${message.slice("unsupported rate selectors:".length).trim()}`;
  }
  if (message.startsWith("cannot settle usage in state ")) {
    return `当前状态无法确认扣款：${message.slice("cannot settle usage in state ".length)}`;
  }
  if (message.startsWith("cannot refund usage in state ")) {
    return `当前状态无法退款：${message.slice("cannot refund usage in state ".length)}`;
  }
  const translated = message
    .replace(/^Field required$/i, "此字段为必填项")
    .replace(/^Input should be a valid integer.*$/i, "请输入有效的整数")
    .replace(/^Input should be greater than or equal to (.+)$/i, "数值不能小于 $1")
    .replace(/^Input should be less than or equal to (.+)$/i, "数值不能大于 $1")
    .replace(
      /^String should have at least (\d+) characters?$/i,
      "至少需要输入 $1 个字符",
    )
    .replace(
      /^String should have at most (\d+) characters?$/i,
      "最多可输入 $1 个字符",
    );
  if (translated !== message || !/[A-Za-z]/.test(translated)) return translated;
  return "操作失败，请稍后重试或联系技术人员";
}

function localizeFieldPath(location) {
  return location
    .filter((part) => part !== "body")
    .map((part) => FIELD_LABELS[part] || part)
    .join(".");
}

function formatApiDetail(detail, fallback) {
  if (typeof detail === "string" && detail) return translateApiText(detail);
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return translateApiText(item);
      if (!item || typeof item !== "object") return String(item);
      const location = Array.isArray(item.loc)
        ? localizeFieldPath(item.loc)
        : "";
      const message = translateApiText(item.msg || item.message || JSON.stringify(item));
      return location ? `${location}：${message}` : message;
    }).filter(Boolean);
    if (messages.length) return messages.join("；");
  }
  if (detail && typeof detail === "object") {
    return translateApiText(detail.message) || JSON.stringify(detail);
  }
  return fallback;
}

async function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const cookieCsrf = readCookie("enmotion_admin_csrf");
      if (cookieCsrf) state.csrf = cookieCsrf;
      const refreshed = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
        body: "{}",
      });
      if (!refreshed.ok) return false;
      const payload = await refreshed.json();
      state.csrf = payload.csrf_token;
      state.authGeneration += 1;
      sessionStorage.setItem("enmotion-admin-csrf", state.csrf);
      return true;
    })().finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

async function api(path, options = {}, retry = true) {
  const cookieCsrf = readCookie("enmotion_admin_csrf");
  if (cookieCsrf) state.csrf = cookieCsrf;
  const requestGeneration = state.authGeneration;
  const headers = new Headers(options.headers || {});
  if (state.csrf && options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", state.csrf);
  }
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: "include" });
  if (response.status === 401 && retry && path !== "/auth/login") {
    if (state.authGeneration !== requestGeneration || await refreshSession()) {
      return api(path, options, false);
    }
  }
  if (!response.ok) {
    let detail = `请求失败（状态码 ${response.status}）`;
    try {
      const payload = await response.json();
      detail = formatApiDetail(payload.detail, detail);
    } catch (_) { /* no body */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function fmt(value) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function escapeText(value) {
  const span = document.createElement("span");
  span.textContent = String(value ?? "");
  return span.innerHTML;
}

function operationLabel(value) {
  return OPERATION_LABELS[value] || value;
}

function systemReasonLabel(value) {
  if (SYSTEM_REASON_LABELS[value]) return SYSTEM_REASON_LABELS[value];
  if (value?.startsWith("reserved for ")) {
    return `为${operationLabel(value.slice("reserved for ".length))}预留额度`;
  }
  if (value?.startsWith("admin reconciliation: ")) {
    return `管理员核对：${value.slice("admin reconciliation: ".length)}`;
  }
  return value;
}

function localizeAuditDetailValue(key, value) {
  if (value === null || value === undefined) return "无";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) {
    return value.map((item) => localizeAuditDetailValue(key, item));
  }
  if (typeof value === "object") return localizeAuditDetail(value);
  if (key === "operation") return operationLabel(value);
  if (key === "role") return ROLE_LABELS[value] || value;
  if (key === "changed_fields") return FIELD_LABELS[value] || "其他字段";
  if (key === "target") return RELEASE_TARGET_LABELS[value] || "其他发布目标";
  if (key === "arch") return ARCH_LABELS[value] || "其他处理器架构";
  if (key === "platform") return PLATFORM_LABELS[value] || "其他系统平台";
  return value;
}

function localizeAuditDetail(detail) {
  if (!detail || typeof detail !== "object") return detail;
  return Object.fromEntries(Object.entries(detail).map(([key, value], index) => [
    AUDIT_DETAIL_KEY_LABELS[key] || FIELD_LABELS[key] || `其他信息 ${index + 1}`,
    localizeAuditDetailValue(key, value),
  ]));
}

function syncRateModels() {
  const form = $("#rate-form");
  const operation = form.querySelector("[name=operation]").value;
  const modelSelect = form.querySelector("[name=model]");
  const prior = modelSelect.value;
  const options = (MODEL_CATALOG[operation] || []).map(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = `${label} — ${value}`;
    return option;
  });
  modelSelect.replaceChildren(...options);
  if (options.some((option) => option.value === prior)) modelSelect.value = prior;
}

function renderProviderConfig(config) {
  state.providerConfig = config;
  const configuredByModel = Object.fromEntries(
    config.models.map((item) => [item.model, item.configured]),
  );
  const configuredCount = config.models.filter((item) => item.configured).length;
  $("#provider-version").textContent = String(config.version);
  $("#provider-configured-count").textContent = String(configuredCount);
  $("#provider-last-check").textContent = config.updated_at
    ? `保存时已验证 · ${new Date(config.updated_at).toLocaleString("zh-CN")}`
    : "尚未检查";
  const source = $("#provider-source");
  source.textContent = config.source === "managed" ? "服务器受管理配置" : "环境变量配置";
  source.classList.toggle("off", configuredCount === 0);
  $("#provider-config-form [name=base_url]").value = config.base_url;

  $("#provider-credential-fields").innerHTML = PROVIDER_MODELS.map(
    ([model, label, capability]) => {
      const configured = Boolean(configuredByModel[model]);
      return `
        <div class="provider-credential-row" data-provider-model="${model}">
          <div class="provider-model-label">
            <strong>${escapeText(label)} · ${escapeText(capability)}</strong>
            <code>${escapeText(model)}</code>
            <span class="status ${configured ? "" : "off"}">${configured ? "已配置" : "未配置"}</span>
          </div>
          <label>
            ${configured ? "替换 API 密钥" : "设置 API 密钥"}
            <input
              data-provider-credential
              type="password"
              autocomplete="new-password"
              spellcheck="false"
              maxlength="16384"
              placeholder="${configured ? "留空以保留当前密钥" : "输入企业 API 密钥"}"
            >
          </label>
          <label class="provider-remove">
            <input data-provider-remove type="checkbox" ${configured ? "" : "disabled"}>
            移除当前密钥
          </label>
        </div>
      `;
    },
  ).join("");

  const warning = $("#provider-write-warning");
  warning.textContent = config.writable
    ? ""
    : "服务器尚未配置独立的加密主密钥。当前环境变量配置仍会继续工作，但无法在此页面保存更改。";
  warning.classList.toggle("hidden", config.writable);
  $("#provider-config-form").querySelectorAll("input, button").forEach((field) => {
    if (field.matches("[data-provider-remove][disabled]")) return;
    if (field.matches("[data-provider-access-check]")) return;
    field.disabled = !config.writable;
  });
}

async function loadProviderConfig() {
  renderProviderConfig(await api("/admin/provider-config"));
}

async function loadUsers() {
  state.users = await api("/admin/users");
  $("#account-count").textContent = state.users.length;
  $("#users-body").innerHTML = state.users.map((user) => `
    <tr>
      <td><strong>${escapeText(user.username)}</strong><br><code>${escapeText(user.id)}</code></td>
      <td>${escapeText(ROLE_LABELS[user.role] || user.role)}</td>
      <td><span class="status ${user.active ? "" : "off"}">${user.active ? "已启用" : "已停用"}</span></td>
      <td>${fmt(user.available_credits)}</td>
      <td>${fmt(user.reserved_credits)}</td>
      <td><div class="actions">
        <button class="secondary" data-action="credit" data-id="${user.id}">调整额度</button>
        <button class="secondary" data-action="status" data-id="${user.id}">${user.active ? "停用" : "重新启用"}</button>
        <button class="secondary" data-action="password" data-id="${user.id}">重置密码</button>
        <button class="secondary danger" data-action="revoke" data-id="${user.id}">撤销会话</button>
      </div></td>
    </tr>
  `).join("");
}

async function loadRates() {
  const rows = await api("/admin/rate-cards");
  const effectiveSignatures = new Set();
  $("#rates-body").innerHTML = rows.map((card) => {
    const selectorSignature = JSON.stringify(
      Object.fromEntries(Object.entries(card.selectors || {}).sort(([left], [right]) => left.localeCompare(right))),
    );
    const signature = `${card.operation}\u0000${card.model}\u0000${selectorSignature}`;
    const effective = card.active && !effectiveSignatures.has(signature);
    if (effective) effectiveSignatures.add(signature);
    const statusText = !card.active ? "已停用" : effective ? "当前生效" : "已启用 · 同条件已被覆盖";
    return `
    <tr><td>${escapeText(operationLabel(card.operation))}</td><td>${escapeText(card.model)}</td>
    <td>${fmt(card.unit_cost)}</td><td><code>${escapeText(JSON.stringify(card.selectors))}</code></td>
    <td><span class="status ${card.active ? "" : "off"}">${statusText}</span><br>
      <small>优先级 ${fmt(card.priority)} · 版本 ${card.version}</small></td>
    <td><button class="secondary danger" data-rate-action="delete" data-rate-id="${escapeText(card.id)}">删除</button></td></tr>
  `;
  }).join("");
}

async function loadLedger() {
  const rows = await api("/admin/ledger?limit=100");
  $("#ledger-body").innerHTML = rows.map((entry) => `
    <tr><td>${new Date(entry.created_at).toLocaleString("zh-CN")}</td><td><code>${escapeText(entry.user_id)}</code></td>
    <td>${escapeText(LEDGER_TYPE_LABELS[entry.entry_type] || entry.entry_type)}</td><td>${fmt(entry.delta_available)}</td>
    <td>${fmt(entry.delta_reserved)}</td><td>${escapeText(systemReasonLabel(entry.reason))}</td></tr>
  `).join("");
}

async function loadPendingUsage() {
  const rows = await api("/admin/usage?usage_status=pending_reconciliation&limit=100");
  $("#pending-body").innerHTML = rows.map((usage) => `
    <tr><td>${new Date(usage.created_at).toLocaleString("zh-CN")}</td>
    <td><code>${escapeText(usage.user_id)}</code><br><small>${escapeText(usage.id)}</small></td>
    <td>${escapeText(operationLabel(usage.operation))}<br><code>${escapeText(usage.model)}</code></td>
    <td>${fmt(usage.reserved_units)}</td><td>${escapeText(usage.error_code ? (ERROR_CODE_LABELS[usage.error_code] || "需要人工核对") : "等待核对")}</td>
    <td><div class="actions">
      <button class="secondary" data-usage-action="settle" data-usage-id="${usage.id}">确认扣款</button>
      <button class="secondary danger" data-usage-action="refund" data-usage-id="${usage.id}">退还额度</button>
    </div></td></tr>
  `).join("");
}

async function loadAudit() {
  const rows = await api("/admin/audit?limit=100");
  $("#audit-body").innerHTML = rows.map((entry) => `
    <tr><td>${new Date(entry.created_at).toLocaleString("zh-CN")}</td><td>${escapeText(AUDIT_ACTION_LABELS[entry.action] || entry.action)}</td>
    <td><code>${escapeText(entry.actor_user_id || "系统")}</code></td>
    <td>${escapeText(AUDIT_TARGET_LABELS[entry.target_type] || entry.target_type)}<br><code>${escapeText(entry.target_id || "")}</code></td>
    <td><code>${escapeText(JSON.stringify(localizeAuditDetail(entry.detail)))}</code></td></tr>
  `).join("");
}

async function loadAll() {
  await Promise.all([
    loadUsers(),
    loadProviderConfig(),
    loadRates(),
    loadLedger(),
    loadPendingUsage(),
    loadAudit(),
  ]);
}

async function showDashboard(session) {
  if (session.user.role !== "admin") throw new Error("需要管理员权限");
  $("#current-user").textContent = session.user.username;
  $("#login-panel").classList.add("hidden");
  $("#dashboard").classList.remove("hidden");
  $("#logout").classList.remove("hidden");
  await loadAll();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!beginFormSubmit(form)) return;
  const values = Object.fromEntries(new FormData(form));
  try {
    const payload = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({ ...values, device_label: "EnMotion 网页管理中心" }),
    });
    state.csrf = payload.csrf_token;
    state.authGeneration += 1;
    sessionStorage.setItem("enmotion-admin-csrf", state.csrf);
    await showDashboard({ user: payload.user });
  } catch (error) { showNotice(error.message, true); }
  finally { endFormSubmit(form); }
});

$("#logout").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST", body: "{}" }); } catch (_) { /* clear UI anyway */ }
  sessionStorage.removeItem("enmotion-admin-csrf");
  location.reload();
});

$("#refresh").addEventListener("click", () => loadAll().catch((error) => showNotice(error.message, true)));
$("#rate-form [name=operation]").addEventListener("change", syncRateModels);
syncRateModels();

$("#create-user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!beginFormSubmit(form)) return;
  const values = Object.fromEntries(new FormData(form));
  values.initial_credits = Number(values.initial_credits);
  try {
    await api("/admin/users", { method: "POST", body: JSON.stringify(values) });
    form.reset();
    await loadUsers();
    showNotice("账号已创建");
  } catch (error) { showNotice(error.message, true); }
  finally { endFormSubmit(form); }
});

$("#provider-config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!beginFormSubmit(form)) return;
  try {
    const baseUrl = form.querySelector("[name=base_url]").value.trim().replace(/\/+$/, "");
    const credentials = {};
    form.querySelectorAll("[data-provider-model]").forEach((row) => {
      const model = row.dataset.providerModel;
      const replacement = row.querySelector("[data-provider-credential]").value.trim();
      const remove = row.querySelector("[data-provider-remove]").checked;
      if (remove) credentials[model] = null;
      else if (replacement) credentials[model] = replacement;
    });
    const payload = { credentials };
    if (baseUrl !== state.providerConfig?.base_url) payload.base_url = baseUrl;
    if (!payload.base_url && Object.keys(credentials).length === 0) {
      throw new Error("没有需要保存的 API 配置更改");
    }
    const updated = await api("/admin/provider-config", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    renderProviderConfig(updated);
    await loadAudit();
    showNotice("共享 API 配置已验证并更新，新请求将立即使用新配置");
  } catch (error) { showNotice(error.message, true); }
  finally { endFormSubmit(form); }
});

$("#provider-check-access").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  if (button.disabled) return;
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = "正在检查…";
  try {
    const result = await api("/admin/provider-config/validate", {
      method: "POST",
      body: "{}",
    });
    $("#provider-last-check").textContent = new Date(result.validated_at).toLocaleString("zh-CN");
    const accessible = new Set(result.configured_models);
    $("#provider-credential-fields").querySelectorAll("[data-provider-model]").forEach((row) => {
      const status = row.querySelector(".status");
      if (accessible.has(row.dataset.providerModel)) {
        status.textContent = "访问已确认";
        status.classList.remove("off");
      }
    });
    showNotice(`模型访问检查通过：${result.configured_models.length} 个已配置模型可用`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.textContent = previous;
    button.disabled = false;
  }
});

$("#rate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!beginFormSubmit(form)) return;
  const values = Object.fromEntries(new FormData(form));
  try {
    values.unit_cost = Number(values.unit_cost);
    values.priority = Number(values.priority);
    try {
      values.selectors = JSON.parse(values.selectors || "{}");
    } catch (_) {
      throw new Error("筛选条件必须是有效的 JSON。");
    }
    await api("/admin/rate-cards", { method: "POST", body: JSON.stringify(values) });
    await loadRates();
    showNotice("计费规则已添加");
  } catch (error) { showNotice(error.message, true); }
  finally { endFormSubmit(form); }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-rate-action]");
  if (!button || button.dataset.rateAction !== "delete") return;
  const cardId = button.dataset.rateId;
  if (!cardId || !confirm("确定删除此计费规则吗？删除后新请求不会再使用它，已有额度记录仍会保留。")) {
    return;
  }
  button.disabled = true;
  try {
    await api(`/admin/rate-cards/${encodeURIComponent(cardId)}`, { method: "DELETE" });
    await Promise.all([loadRates(), loadAudit()]);
    showNotice("计费规则已删除");
  } catch (error) {
    showNotice(error.message, true);
    button.disabled = false;
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const user = state.users.find((item) => item.id === button.dataset.id);
  if (!user) return;
  const action = button.dataset.action;
  try {
    if (action === "status") {
      await api(`/admin/users/${user.id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ active: !user.active }),
      });
      await loadUsers();
      return;
    }
    if (action === "revoke") {
      if (!confirm(`确定要撤销 ${user.username} 的所有登录会话吗？`)) return;
      await api(`/admin/users/${user.id}/sessions/revoke`, { method: "POST", body: "{}" });
      showNotice("登录会话已撤销");
      return;
    }
    const dialog = $("#action-dialog");
    $("#action-title").textContent = action === "credit"
      ? `调整 ${user.username} 的额度`
      : `重置 ${user.username} 的密码`;
    dialog.querySelector("[name=user_id]").value = user.id;
    dialog.querySelector("[name=action]").value = action;
    dialog.querySelector("[name=idempotency_key]").value = crypto.randomUUID();
    dialog.querySelector("[name=password]").value = "";
    $("#amount-label").classList.toggle("hidden", action !== "credit");
    $("#reason-label").classList.toggle("hidden", action !== "credit");
    $("#password-label").classList.toggle("hidden", action !== "password");
    dialog.querySelector("[name=amount]").required = action === "credit";
    dialog.querySelector("[name=reason]").required = action === "credit";
    dialog.querySelector("[name=password]").required = action === "password";
    dialog.showModal();
  } catch (error) { showNotice(error.message, true); }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-usage-action]");
  if (!button) return;
  const action = button.dataset.usageAction;
  const usageId = button.dataset.usageId;
  const reason = window.prompt(
    action === "settle"
      ? "确认此用量的扣款。请输入向服务提供商核实后的原因："
      : "退还此用量的额度。请输入向服务提供商核实后的原因：",
  );
  if (!reason?.trim()) return;
  try {
    await api(`/admin/usage/${encodeURIComponent(usageId)}/${action}`, {
      method: "POST",
      body: JSON.stringify({ reason: reason.trim(), charged_units: null }),
    });
    await loadAll();
    showNotice(action === "settle" ? "已确认用量扣款" : "已退还用量额度");
  } catch (error) { showNotice(error.message, true); }
});

$("#action-form").addEventListener("submit", async (event) => {
  const submitter = event.submitter;
  if (submitter?.value === "cancel") return;
  event.preventDefault();
  const form = event.currentTarget;
  if (!beginFormSubmit(form)) return;
  const values = Object.fromEntries(new FormData(form));
  let mutationSucceeded = false;
  try {
    if (values.action === "credit") {
      await api(`/admin/users/${values.user_id}/credits`, {
        method: "POST",
        body: JSON.stringify({
          delta: Number(values.amount),
          reason: values.reason,
          idempotency_key: values.idempotency_key,
        }),
      });
    } else {
      await api(`/admin/users/${values.user_id}/password`, {
        method: "POST",
        body: JSON.stringify({ new_password: values.password }),
      });
    }
    mutationSucceeded = true;
  } catch (error) { showNotice(error.message, true); }
  finally { endFormSubmit(form); }
  if (!mutationSucceeded) return;

  $("#action-dialog").close();
  form.reset();
  showNotice("账号已更新");
  try {
    await loadAll();
  } catch (error) {
    showNotice(`账号已更新，但刷新列表失败：${error.message}`, true);
  }
});

$("#action-dialog").addEventListener("close", () => {
  $("#action-form").reset();
});

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
  document.querySelectorAll(".dashboard-panel").forEach((panel) => panel.classList.toggle("hidden", panel.id !== tab.dataset.panel));
}));

document.addEventListener("invalid", (event) => {
  const field = event.target;
  if (!(field instanceof HTMLInputElement
    || field instanceof HTMLSelectElement
    || field instanceof HTMLTextAreaElement)) return;
  field.setCustomValidity("");
  if (field.validity.valueMissing) {
    field.setCustomValidity("请填写此字段。");
  } else if (field.validity.tooShort) {
    field.setCustomValidity(`请至少输入 ${field.minLength} 个字符。`);
  } else if (field.validity.tooLong) {
    field.setCustomValidity(`最多可输入 ${field.maxLength} 个字符。`);
  } else if (field.validity.rangeUnderflow) {
    field.setCustomValidity(`数值不能小于 ${field.min}。`);
  } else if (field.validity.rangeOverflow) {
    field.setCustomValidity(`数值不能大于 ${field.max}。`);
  } else if (field.validity.typeMismatch || field.validity.badInput) {
    field.setCustomValidity("请输入有效内容。");
  }
}, true);

document.addEventListener("input", (event) => {
  if (event.target?.setCustomValidity) event.target.setCustomValidity("");
});

document.addEventListener("change", (event) => {
  if (event.target?.setCustomValidity) event.target.setCustomValidity("");
});

api("/auth/session")
  .then(showDashboard)
  .catch(() => { /* expected before first login */ });
