"use strict";

const REQUEST_TIMEOUT_MS = 30000;
const CAPTCHA_IMAGE_TIMEOUT_MS = 8000;

class ApiError extends Error {
  constructor(message, { status = 0, code = "UNKNOWN_ERROR", retryable = false } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

const loginState = {
  captcha: null,
  captchaStatus: "idle",
  captchaFailureMessage: "",
  points: [],
  loadingCaptcha: false,
  submitting: false,
  uiCacheToken: "",
};

const loginElements = {
  form: document.querySelector("#loginForm"),
  studentId: document.querySelector("#studentId"),
  password: document.querySelector("#password"),
  passwordToggle: document.querySelector("#passwordToggle"),
  cardKey: document.querySelector("#cardKey"),
  phaseNotice: document.querySelector("#phaseNotice"),
  stage: document.querySelector("#captchaStage"),
  image: document.querySelector("#captchaImage"),
  markers: document.querySelector("#captchaMarkers"),
  progress: document.querySelector("#captchaProgress"),
  statusTitle: document.querySelector("#captchaStatusTitle"),
  statusDetail: document.querySelector("#captchaStatusDetail"),
  undo: document.querySelector("#undoPoint"),
  refresh: document.querySelector("#refreshCaptcha"),
  message: document.querySelector("#loginMessage"),
  submit: document.querySelector("#loginButton"),
};

const captchaStatusCopy = {
  idle: ["准备获取验证码", "正在准备本地登录环境。"],
  loading: ["正在获取验证码", "正在连接学校选课系统，请稍候。"],
  ready: ["验证码已就绪", "请按顶部提示依次点击四个汉字。"],
  unavailable: [
    "当前时段暂无验证码",
    "学校当前没有返回登录验证码，请等待选课开放或维护结束后再试。",
  ],
  error: ["验证码加载失败", "本次加载已经停止，请查看下方提示后手动重试。"],
};

function setLoginMessage(message, success = false) {
  loginElements.message.textContent = message || "";
  loginElements.message.classList.toggle("is-success", success);
}

function updateLoginControls() {
  const captchaReady = loginState.captchaStatus === "ready" && Boolean(loginState.captcha);
  const busy = loginState.loadingCaptcha || loginState.submitting;
  loginElements.refresh.disabled = busy;
  loginElements.undo.disabled = busy || !captchaReady || loginState.points.length === 0;
  loginElements.submit.disabled = loginState.submitting || !captchaReady;
}

function setCaptchaStatus(status, title = "", detail = "") {
  const fallback = captchaStatusCopy[status] || captchaStatusCopy.error;
  loginState.captchaStatus = status;
  loginElements.stage.dataset.state = status;
  loginElements.stage.setAttribute("aria-busy", String(status === "loading"));
  loginElements.stage.setAttribute("aria-disabled", String(status !== "ready"));
  loginElements.statusTitle.textContent = title || fallback[0];
  loginElements.statusDetail.textContent = detail || fallback[1];
  loginElements.refresh.textContent = status === "ready" ? "刷新验证码" : "重新获取验证码";
  updateLoginControls();
}

function versionedPage(path) {
  const queryToken = new URLSearchParams(window.location.search).get("ui") || "";
  const token = loginState.uiCacheToken || queryToken;
  if (!token) return path;
  const url = new URL(path, window.location.origin);
  url.searchParams.set("ui", token);
  return `${url.pathname}${url.search}`;
}

async function readJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      ...options,
      signal: controller.signal,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new ApiError(data.message || data.detail || "请求失败，请稍后重试", {
        status: response.status,
        code: data.error_code || "HTTP_ERROR",
        retryable: Boolean(data.retryable),
      });
    }
    return data;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ApiError("请求超过 30 秒，本次操作已停止。请检查网络后手动重试。", {
        code: "CLIENT_TIMEOUT",
        retryable: true,
      });
    }
    if (error instanceof ApiError) throw error;
    throw new ApiError("无法连接本地程序，请确认启动终端仍在运行。", {
      code: "CLIENT_NETWORK_ERROR",
      retryable: true,
    });
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function renderCaptchaPoints() {
  loginElements.markers.replaceChildren();
  for (const [index, point] of loginState.points.entries()) {
    const marker = document.createElement("span");
    marker.className = "captcha-marker";
    marker.textContent = String(index + 1);
    marker.style.left = `${(point[0] / 250) * 100}%`;
    marker.style.top = `${(point[1] / 80) * 100}%`;
    loginElements.markers.append(marker);
  }
  loginElements.progress.textContent = `${loginState.points.length} / 4`;
  updateLoginControls();
}

function clearCaptchaPoints() {
  loginState.points = [];
  renderCaptchaPoints();
}

function clearCaptchaImage() {
  loginElements.image.onload = null;
  loginElements.image.onerror = null;
  loginElements.image.removeAttribute("src");
}

function validateCaptchaPayload(captcha) {
  const valid =
    captcha &&
    typeof captcha === "object" &&
    typeof captcha.vtoken === "string" &&
    captcha.vtoken.length > 0 &&
    captcha.vtoken.length <= 512 &&
    typeof captcha.cookie === "string" &&
    captcha.cookie.length > 0 &&
    captcha.cookie.length <= 8192 &&
    typeof captcha.imageUrl === "string" &&
    captcha.imageUrl.startsWith("data:image/") &&
    captcha.imageUrl.length <= 3 * 1024 * 1024;
  if (!valid) {
    throw new ApiError("学校验证码数据不完整，本次加载已停止。请手动重试。", {
      code: "CAPTCHA_INVALID_RESPONSE",
      retryable: true,
    });
  }
  return captcha;
}

function loadCaptchaImage(source) {
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      cleanup();
      reject(
        new ApiError("验证码图片解析超时，本次加载已停止。请手动重试。", {
          code: "CAPTCHA_IMAGE_TIMEOUT",
          retryable: true,
        }),
      );
    }, CAPTCHA_IMAGE_TIMEOUT_MS);

    function cleanup() {
      window.clearTimeout(timeoutId);
      loginElements.image.onload = null;
      loginElements.image.onerror = null;
    }

    loginElements.image.onload = () => {
      cleanup();
      resolve();
    };
    loginElements.image.onerror = () => {
      cleanup();
      reject(
        new ApiError("验证码图片无法显示，本次加载已停止。请手动重试。", {
          code: "CAPTCHA_IMAGE_ERROR",
          retryable: true,
        }),
      );
    };
    loginElements.image.src = source;
  });
}

function describeCaptchaFailure(error) {
  const code = error instanceof ApiError ? error.code : "UNKNOWN_ERROR";
  const message = error instanceof Error ? error.message : "验证码加载失败，请手动重试。";
  if (code === "CAPTCHA_UNAVAILABLE") {
    return {
      status: "unavailable",
      title: "当前时段暂无验证码",
      detail: "学校未开放验证码接口。请等待预选、复选、补选开放或维护结束后再试。",
      message,
    };
  }
  if (["CAPTCHA_TIMEOUT", "CLIENT_TIMEOUT", "CAPTCHA_IMAGE_TIMEOUT"].includes(code)) {
    return {
      status: "error",
      title: "验证码请求超时",
      detail: "本次加载已经停止，不会在后台自动循环。请检查网络后手动重试。",
      message,
    };
  }
  if (code === "CAPTCHA_NETWORK_ERROR") {
    return {
      status: "error",
      title: "学校系统暂不可达",
      detail: "本次加载已经停止。请检查网络，或等待学校服务恢复后手动重试。",
      message,
    };
  }
  if (code === "CLIENT_NETWORK_ERROR") {
    return {
      status: "error",
      title: "本地服务连接失败",
      detail: "请确认启动程序的终端窗口仍然开启，然后刷新本页。",
      message,
    };
  }
  if (["CAPTCHA_INVALID_RESPONSE", "CAPTCHA_IMAGE_ERROR"].includes(code)) {
    return {
      status: "error",
      title: "验证码响应异常",
      detail: "学校没有返回可用的验证码。本次加载已经停止，请稍后手动重试。",
      message,
    };
  }
  return {
    status: "error",
    title: "验证码加载失败",
    detail: "本次加载已经停止，请查看下方原因后手动重试。",
    message,
  };
}

async function loadCaptcha() {
  if (loginState.loadingCaptcha) return false;
  loginState.loadingCaptcha = true;
  loginState.captcha = null;
  loginState.captchaFailureMessage = "";
  clearCaptchaPoints();
  clearCaptchaImage();
  setCaptchaStatus("loading");
  setLoginMessage("");

  try {
    const captcha = validateCaptchaPayload(await requestJson(`/api/captcha?t=${Date.now()}`));
    await loadCaptchaImage(captcha.imageUrl);
    loginState.captcha = captcha;
    setCaptchaStatus("ready");
    return true;
  } catch (error) {
    loginState.captcha = null;
    clearCaptchaImage();
    const failure = describeCaptchaFailure(error);
    loginState.captchaFailureMessage = failure.message;
    setCaptchaStatus(failure.status, failure.title, failure.detail);
    setLoginMessage(failure.message);
    return false;
  } finally {
    loginState.loadingCaptcha = false;
    updateLoginControls();
  }
}

function addCaptchaPoint(event) {
  if (
    loginState.captchaStatus !== "ready" ||
    !loginState.captcha ||
    loginState.points.length >= 4
  ) {
    return;
  }

  const rect = loginElements.image.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const x = Math.max(0, Math.min(250, Math.round(((event.clientX - rect.left) / rect.width) * 250)));
  const y = Math.max(0, Math.min(80, Math.round(((event.clientY - rect.top) / rect.height) * 80)));
  loginState.points.push([x, y]);
  renderCaptchaPoints();
}

async function submitLogin(event) {
  event.preventDefault();
  if (loginState.submitting) return;

  const studentId = loginElements.studentId.value.trim();
  const password = loginElements.password.value;
  const cardKey = loginElements.cardKey.value.trim();
  if (!/^\d{6,12}$/.test(studentId)) {
    setLoginMessage("请输入 6 至 12 位数字学号");
    loginElements.studentId.focus();
    return;
  }
  if (!password) {
    setLoginMessage("请输入选课系统密码");
    loginElements.password.focus();
    return;
  }
  if (!cardKey.startsWith("SZU3.")) {
    setLoginMessage("请输入终端生成的 Card Key V3");
    loginElements.cardKey.focus();
    return;
  }
  if (!loginState.captcha || loginState.captchaStatus !== "ready") {
    setLoginMessage(loginState.captchaFailureMessage || "请先成功获取验证码");
    return;
  }
  if (loginState.points.length !== 4) {
    setLoginMessage("请按顺序完成四个验证码点击点");
    return;
  }

  loginState.submitting = true;
  updateLoginControls();
  loginElements.submit.textContent = "正在验证登录";
  setLoginMessage("正在连接学校选课系统...", true);

  try {
    const result = await requestJson("/api/login", {
      method: "POST",
      body: JSON.stringify({
        student_id: studentId,
        password,
        card_key: cardKey,
        vtoken: loginState.captcha.vtoken,
        verifyCode: loginState.points,
        cookie: loginState.captcha.cookie,
      }),
    });
    setLoginMessage(result.message || "登录成功", true);
    window.location.assign(versionedPage("/"));
  } catch (error) {
    const failureMessage = error instanceof Error ? error.message : "登录失败";
    const refreshed = await loadCaptcha();
    if (refreshed) {
      setLoginMessage(`${failureMessage}；验证码已更新，请重新点击四个汉字。`);
    } else {
      const captchaMessage = loginState.captchaFailureMessage || "新验证码获取失败，请手动重试。";
      setLoginMessage(`${failureMessage}；${captchaMessage}`);
    }
  } finally {
    loginState.submitting = false;
    loginElements.submit.textContent = "登录并进入工作台";
    updateLoginControls();
  }
}

async function initializeLogin() {
  setCaptchaStatus("idle");
  try {
    const [bootstrap, session] = await Promise.all([
      requestJson("/api/bootstrap"),
      requestJson("/api/session"),
    ]);
    loginState.uiCacheToken = bootstrap.ui_cache_token || "";
    if (session.logged_in) {
      window.location.replace(versionedPage("/"));
      return;
    }
    loginElements.studentId.value = bootstrap.student_id || "";
    loginElements.cardKey.value = bootstrap.card_key || "";
    if (bootstrap.phase_notice) {
      loginElements.phaseNotice.textContent = bootstrap.phase_notice;
    }
  } catch (error) {
    setLoginMessage(error instanceof Error ? error.message : "本地登录信息加载失败");
  }
  await loadCaptcha();
}

loginElements.stage.addEventListener("click", addCaptchaPoint);
loginElements.undo.addEventListener("click", () => {
  loginState.points.pop();
  renderCaptchaPoints();
});
loginElements.refresh.addEventListener("click", loadCaptcha);
loginElements.form.addEventListener("submit", submitLogin);
loginElements.passwordToggle.addEventListener("click", () => {
  const hidden = loginElements.password.type === "password";
  loginElements.password.type = hidden ? "text" : "password";
  loginElements.passwordToggle.textContent = hidden ? "隐藏" : "显示";
  loginElements.passwordToggle.setAttribute("aria-label", hidden ? "隐藏密码" : "显示密码");
});

initializeLogin();
