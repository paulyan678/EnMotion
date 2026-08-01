from __future__ import annotations

from pathlib import Path

ADMIN_SCRIPT = Path(__file__).parents[1] / "app" / "static" / "admin" / "app.js"
PROJECT_ROOT = Path(__file__).parents[1]


def test_admin_uses_the_official_brand_assets() -> None:
    static_root = PROJECT_ROOT / "app" / "static" / "admin"
    markup = (static_root / "index.html").read_text(encoding="utf-8")

    assert 'href="/admin/favicon.ico"' in markup
    assert 'src="/admin/logo.svg"' in markup
    assert 'class="brand-heading"' in markup
    assert (static_root / "favicon.ico").stat().st_size > 0
    logo = (static_root / "logo.svg").read_text(encoding="utf-8")
    assert "#34D8C4" in logo
    assert "<text" not in logo


def test_async_submit_handlers_keep_a_stable_form_reference() -> None:
    """DOM SubmitEvent.currentTarget becomes null after the first await."""

    script = ADMIN_SCRIPT.read_text(encoding="utf-8")

    assert script.count("const form = event.currentTarget;") >= 2
    assert "event.currentTarget.reset()" not in script
    assert "new FormData(form)" in script
    assert "form.reset()" in script


def test_admin_bootstraps_csrf_and_formats_structured_api_errors() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")

    assert 'readCookie("enmotion_admin_csrf")' in script
    assert "function formatApiDetail" in script
    assert "Array.isArray(detail)" in script
    assert "formatApiDetail(payload.detail, detail)" in script
    assert "let refreshPromise = null;" in script
    assert "async function refreshSession()" in script
    assert "state.authGeneration !== requestGeneration" in script
    assert "function beginFormSubmit" in script
    assert "function endFormSubmit" in script


def test_credit_adjustment_retries_keep_one_idempotency_key() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")
    markup = (Path(__file__).parents[1] / "app" / "static" / "admin" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'name="idempotency_key" type="hidden"' in markup
    assert 'dialog.querySelector("[name=idempotency_key]").value = crypto.randomUUID();' in script
    assert "idempotency_key: values.idempotency_key" in script
    action_handler = script.split(
        '$("#action-form").addEventListener("submit"',
        maxsplit=1,
    )[1]
    assert "let mutationSucceeded = false;" in action_handler
    assert "if (!mutationSucceeded) return;" in action_handler
    assert "账号已更新，但刷新列表失败" in action_handler


def test_rate_card_models_are_fixed_and_operation_aware() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")
    markup = (Path(__file__).parents[1] / "app" / "static" / "admin" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "const MODEL_CATALOG" in script
    for model in (
        "gpt-image-2",
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
        "doubao-seedance-2-0-mini-260615",
        "deepseek-v4-flash",
        "qwen3.7-max",
        "deepseek-v4-pro",
    ):
        assert model in script
    assert 'select name="model"' in markup
    assert "syncRateModels" in script


def test_rate_cards_explain_precedence_and_can_be_deleted_safely() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")
    markup = (PROJECT_ROOT / "app" / "static" / "admin" / "index.html").read_text(encoding="utf-8")

    assert "优先级相同时选择版本最新的一项" in markup
    assert "已有额度记录会完整保留" in markup
    assert 'data-rate-action="delete"' in script
    assert 'method: "DELETE"' in script
    assert "当前生效" in script
    assert "同条件已被覆盖" in script


def test_provider_configuration_ui_never_requests_existing_secret_values() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")
    markup = (Path(__file__).parents[1] / "app" / "static" / "admin" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "API 配置" in markup
    assert "共享 API 配置" in markup
    assert 'type="password"' in markup or 'type="password"' in script
    assert "留空表示保留当前密钥" in markup
    assert "/admin/provider-config" in script
    assert "data-provider-remove" in script
    assert "config.models.map" in script
    assert "credential_value" not in markup
    assert "credential_value" not in script


def test_pending_usage_has_an_explicit_reconciliation_workflow() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")
    markup = (Path(__file__).parents[1] / "app" / "static" / "admin" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "pending-body" in markup
    assert "/admin/usage?usage_status=pending_reconciliation" in script
    assert 'data-usage-action="settle"' in script
    assert 'data-usage-action="refund"' in script


def test_admin_interface_is_simplified_chinese_except_model_names() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")
    markup = (Path(__file__).parents[1] / "app" / "static" / "admin" / "index.html").read_text(
        encoding="utf-8"
    )

    assert '<html lang="zh-CN">' in markup
    assert "<title>EnMotion 管理中心</title>" in markup
    assert "企业管理控制台" not in markup
    assert '<h1 aria-label="EnMotion 管理中心">' in markup
    assert '<span class="brand-motion">Motion</span>' in markup
    assert '<span class="brand-context">管理中心</span>' in markup
    for text in (
        "管理员登录",
        "账号管理",
        "API 配置",
        "计费规则",
        "额度明细",
        "审计记录",
        "创建账号",
        "AI 模型",
        "待核对记录",
    ):
        assert text in markup

    for text in (
        "账号已创建",
        "共享 API 配置已更新",
        "计费规则已添加",
        "登录会话已撤销",
        "已确认用量扣款",
        "已退还用量额度",
        "请填写此字段。",
        "请求失败（状态码",
        '"chat.completions": "文本对话"',
        '"images.generations": "图像生成"',
        '"images.edits": "图像编辑"',
        '"video.generations": "视频生成"',
        '"admin.user_created": "创建账号"',
        "function localizeAuditDetail",
        '"provider accepted request": "服务提供商已接受请求"',
        'ambiguous_provider_transport_error: "服务提供商传输结果不明确，等待核对"',
        '"需要人工核对"',
        "版本 ${card.version}",
    ):
        assert text in script

    for operation_value, operation_label in (
        ("chat.completions", "文本对话"),
        ("images.generations", "图像生成"),
        ("images.edits", "图像编辑"),
        ("video.generations", "视频生成"),
    ):
        assert f'<option value="{operation_value}">{operation_label}</option>' in markup

    for english_ui_text in (
        "Administrator sign in",
        "Create account",
        "Managed accounts",
        "Rate cards",
        "Pending reconciliation",
        "Recent credit entries",
        "Administrative audit",
        "Sign out",
        "Account created",
        "Rate card added",
        "Sessions revoked",
    ):
        assert english_ui_text not in markup
        assert english_ui_text not in script

    for model_name in (
        "DeepSeek V4 Flash",
        "Qwen 3.7 Max",
        "GPT Image 2",
        "Seedance 2.0",
    ):
        assert model_name in script


def test_admin_localizes_api_and_browser_validation_messages() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")

    assert "function translateApiText" in script
    assert "function localizeFieldPath" in script
    assert '"invalid username or password": "用户名或密码错误"' in script
    assert "Field required" in script
    assert "操作失败，请稍后重试或联系技术人员" in script
    assert 'document.addEventListener("invalid"' in script
    assert 'field.setCustomValidity("请填写此字段。")' in script
    assert 'new Intl.NumberFormat("zh-CN")' in script
    assert 'toLocaleString("zh-CN")' in script


def test_admin_reset_password_is_visible_and_accepts_six_characters() -> None:
    script = ADMIN_SCRIPT.read_text(encoding="utf-8")
    markup = (Path(__file__).parents[1] / "app" / "static" / "admin" / "index.html").read_text(
        encoding="utf-8"
    )

    assert (
        '<label id="password-label">新密码 <input name="password" type="text" '
        'minlength="6" maxlength="256"'
    ) in markup
    assert ('<label>密码 <input name="password" type="password" required minlength="12">') in markup
    assert '"password must contain at least 6 characters": "密码至少需要 6 个字符"' in script
    assert 'dialog.querySelector("[name=password]").value = "";' in script
    assert '$("#action-dialog").addEventListener("close"' in script
    assert '$("#action-form").reset();' in script


def test_production_configs_do_not_log_release_capabilities_or_csrf() -> None:
    service = (PROJECT_ROOT / "deploy" / "enmotion-control.service").read_text(encoding="utf-8")
    caddy = (PROJECT_ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    caddy_override = (PROJECT_ROOT / "deploy" / "enmotion-caddy.override.conf").read_text(
        encoding="utf-8"
    )
    caddy_environment = (PROJECT_ROOT / "deploy" / "enmotion-caddy.env.example").read_text(
        encoding="utf-8"
    )
    package_config = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "--no-access-log" in service
    assert "--loop asyncio" in service
    assert "log_skip /api/v1/releases/session/*" in caddy
    assert "request>uri delete" in caddy
    assert "request>headers delete" in caddy
    assert "request>headers>X-CSRF-Token delete" in caddy
    assert "EnvironmentFile=/etc/enmotion-caddy.env" in caddy_override
    assert "ENMOTION_DOMAIN=accounts.example.com" in caddy_environment
    assert "ACME_EMAIL=admin@example.com" in caddy_environment
    assert '[tool.setuptools.package-data]\napp = ["static/admin/*"]' in package_config
