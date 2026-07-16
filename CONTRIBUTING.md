# 贡献指南

感谢你愿意改进深大抢课助手。这个项目直接对接学校系统，任何改动都应把安全边界和请求兼容性放在首位。

## 开发环境

项目要求 Python 3.13。推荐使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

完整 OCR 运行环境可使用：

```powershell
python -m pip install -r requirements.txt
```

## 提交前检查

```powershell
python -m ruff check .
python -m ruff format --check .
python -m compileall -q .
python -m pytest -q
node --check static_dist/login.js
node --check static_dist/course-app.js
```

测试通过 `tests/conftest.py` 拦截所有未模拟的外部 `requests` 请求。新增测试不得访问深圳大学选课系统，也不得使用真实账号、Cookie、token、Card Key 或验证码。

## 改动原则

- 不提交 `*.pem`、`.env*`、数据库、日志、验证码图片或打包产物。
- 不随意改动 `choose_course.py` 和 `course_list.py` 中的学校 URL、表单字段及 `addParam` 格式。
- 修改学校请求协议时，必须同步补充固定契约测试。
- 阶段识别必须保持保守策略：未知、未开放、已结束或无法确认时禁止启动自动抢课。
- 前端错误提示应区分登录失败、会话过期、非开放期、网络超时和学校响应异常。
- 保持本地服务只监听 `127.0.0.1`，除非有完整的鉴权和部署安全设计。

## Pull Request

PR 请说明改动目的、用户影响、风险边界和验证命令。涉及学校请求、会话恢复、Card Key 或抢课循环的变更，应额外说明兼容性与失败回退行为。
