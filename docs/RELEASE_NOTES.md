## 无需 Python，完整解压后即可使用

本版本提供 Windows、macOS 和 Linux 原生发布包。普通用户请下载与自己系统匹配的 ZIP，不要下载 GitHub 自动附带的 Source code 压缩包。

| 系统 | 下载文件 | 启动方式 |
| --- | --- | --- |
| Windows 10/11 64 位 | `SZU-Course-Help-v3.2.0-windows-x64.zip` | 双击 `启动抢课助手.bat` |
| Apple 芯片 Mac | `SZU-Course-Help-v3.2.0-macos-arm64.zip` | 双击 `启动抢课助手.command` |
| Intel 芯片 Mac | `SZU-Course-Help-v3.2.0-macos-x64.zip` | 双击 `启动抢课助手.command` |
| Linux 64 位 | `SZU-Course-Help-v3.2.0-linux-x64.zip` | 运行 `启动抢课助手.sh` |

每个发布包均含可执行程序、平台启动脚本、`使用手册.md`、`使用手册.pdf`、项目说明、许可证和包内文件校验清单。`SZU-Course-Help-v3.2.0-source.zip` 供开发者使用。

## 首次运行

1. 完整解压 ZIP，不要在压缩包预览窗口内直接运行，也不要只拖出主程序。
2. 运行平台启动脚本，在终端输入学号并生成本机 Card Key。
3. 输入 `Y` 后，程序会启动本地网页并打开登录页。
4. 首次登录由用户输入学校密码并手动完成点击验证码。
5. 预选、未开放、已结束和未知阶段禁止启动自动选课；只有学校返回明确允许的复选、正选、补选或补退选批次才可在二次确认后启动。

## 安全与兼容性

- 本项目暂未购买 Windows 或 Apple 商业代码签名证书，SmartScreen 或 Gatekeeper 可能显示未知开发者提示。
- 只从本仓库 Release 下载，并用 `SHA256SUMS.txt` 核对压缩包。
- 首次运行会在解压目录生成本机 Card Key 密钥和本地清单数据库，请勿公开、上传网盘或提交到 Git。
- Windows 包在 Windows x64 原生构建，两个 macOS 包分别在 Apple Silicon 与 Intel 原生构建，Linux 包在 Ubuntu 22.04 x64 构建。
- 学校接口和规则可能变化，关键选课结果以学校官方系统为准。

完整步骤、系统拦截处理和常见问题请阅读发布包中的 `使用手册.pdf`。
