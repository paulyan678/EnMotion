# EnMotion 用户手册

## 安装与登录

1. 打开公开的 [EnMotion GitHub Releases 下载页](https://github.com/paulyan678/EnMotion/releases/latest)。
2. Apple 芯片 Mac 下载 `EnMotion-<版本>-macOS-arm64.dmg`，Intel Mac 下载
   `EnMotion-<版本>-macOS-x64.dmg`，Windows x64 下载
   `EnMotion-Setup-<版本>-Windows-x64.exe`。不要把 `.app.tar.gz`、`.sig`、
   `.cdx.json` 或 `control-plane-releases.json` 当作首次安装包。
3. macOS 打开 DMG 并把 EnMotion 拖入“应用程序”；Windows 运行签名安装程序。
4. 启动 EnMotion，输入管理员分配的用户名和临时密码。
5. 首次登录后按提示修改密码。

GitHub 下载公开且不需要 Git；EnMotion 的使用权限仍由公司账户控制。员工电脑不需要
Python、Node.js，也不需要填写公司的 AI Provider API Key。

## 账户与额度

顶部账户区域显示当前余额。每次图像、视频或其他收费的 AI 操作在提交前都会显示或使用服务器定义的费率：

- 请求提交前预留额度。
- 成功后结算实际额度。
- 明确失败时退回预留额度。
- 网络结果不明确时显示为“待核对”，不会自动重复提交。

余额不足、账户停用或登录失效时，新的收费请求会停止。本地项目浏览、编辑与已生成文件不会因此被删除。

## 本地数据

默认生成目录：

```text
应用数据/Application Data/
  enmotion-output/
    accounts/
      <账户 ID>/
        output/
```

这里保存项目生成的图片、视频、导出文件、上传副本和缩略图。可以在设置中选择另一个稳定目录。

应用设置、缓存与日志存放在：

| 系统 | 位置 |
|---|---|
| macOS | `~/Library/Application Support/com.enmotion.desktop` |
| Windows | EnMotion 的系统应用数据目录 |

登录时只会将可撤销的刷新令牌保存到 EnMotion 专属应用数据目录；macOS/Linux
目录权限为 `0700`，令牌文件权限为 `0600`。EnMotion 不保存账户密码，也不会把
登录信息写入项目文件、浏览器 localStorage 或 `AGENTS.md`。

EnMotion 不会自动读取、移动或删除其他应用的数据。

## 更新

EnMotion 会在启动完成后静默检查更新。

- 没有更新时，界面不会显示额外提示。
- 有新版本时，顶部会出现小型“有可用更新”按钮。
- 点击后立即后台下载，下载期间可以继续使用。
- EnMotion 保存当前项目和待恢复任务后安装更新并重新启动。
- 正在运行且无法安全暂停的本地导出会先完成，再自动重启。
- 更新不会删除登录、设置、项目或 `enmotion-output`。

也可以在“设置 → 关于 EnMotion”中手动检查更新。

## 管理员

管理员通过服务器上的 EnMotion 管理中心：

- 创建、停用或重新启用员工
- 重置密码
- 撤销设备或登录会话
- 增加或扣减额度，并填写原因
- 查看余额、用量、费率、账本和审计记录
- 控制可用模型和服务器端 Provider 凭证

没有公开注册入口。完整 Provider 密钥不会传到员工电脑。

## 常见问题

### 无法生成，但项目仍能打开

检查账户余额、网络和账户状态。控制服务器暂时不可用时，EnMotion 会保留本地编辑能力，但会安全阻止新的收费请求。

### 更新后文件在哪里

仍在 EnMotion 应用数据目录中的 `enmotion-output`，或你选择的自定义输出目录。
macOS 默认位置是 `~/Library/Application Support/com.enmotion.desktop/enmotion-output`，
不需要“文稿”或“完全磁盘访问”权限。安装程序只替换应用二进制。

### 两个人使用同一台电脑

每个服务器账户使用独立账户 ID 目录和浏览器状态。切换账户前，EnMotion 会终止旧本地会话，避免后台任务计入新账户。

### 如何报告问题

在“设置 → 关于 EnMotion”复制版本号和不含敏感信息的诊断摘要。不要发送密码、完整日志、Provider Key、会话令牌或公司私有素材。
