"use strict";

const REQUEST_TIMEOUT_MS = 30000;

const loginState = {
  captcha: null,
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
  undo: document.querySelector("#undoPoint"),
  refresh: document.querySelector("#refreshCaptcha"),
  message: document.querySelector("#loginMessage"),
  submit: document.querySelector("#loginButton"),
};

function setLoginMessage(message, success = false) {
  loginElements.message.textContent = message || "";
  loginElements.message.classList.toggle("is-success", success);
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
      throw new Error(data.message || data.detail || "请求失败，请稍后重试");
    }
    return data;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("请求超时，请检查网络后重试");
    throw error;
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
  loginElements.undo.disabled = loginState.points.length === 0;
}

function clearCaptchaPoints() {
  loginState.points = [];
  renderCaptchaPoints();
}

async function loadCaptcha() {
  if (loginState.loadingCaptcha) return false;
  loginState.loadingCaptcha = true;
  loginState.captcha = null;
  clearCaptchaPoints();
  loginElements.stage.setAttribute("aria-busy", "true");
  loginElements.refresh.disabled = true;
  setLoginMessage("");

  try {
    const captcha = await requestJson(`/api/captcha?t=${Date.now()}`);
    loginState.captcha = captcha;
    await new Promise((resolve, reject) => {
      loginElements.image.onload = resolve;
      loginElements.image.onerror = () => reject(new Error("验证码图片加载失败"));
      loginElements.image.src = captcha.imageUrl;
    });
    loginElements.stage.setAttribute("aria-busy", "false");
    return true;
  } catch (error) {
    loginElements.stage.setAttribute("aria-busy", "true");
    setLoginMessage(error.message);
    return false;
  } finally {
    loginState.loadingCaptcha = false;
    loginElements.refresh.disabled = false;
  }
}

function addCaptchaPoint(event) {
  if (
    loginElements.stage.getAttribute("aria-busy") === "true" ||
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
  if (!loginState.captcha || loginState.points.length !== 4) {
    setLoginMessage("请按顺序完成四个验证码点击点");
    return;
  }

  loginState.submitting = true;
  loginElements.submit.disabled = true;
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
    const failureMessage = error.message;
    const refreshed = await loadCaptcha();
    setLoginMessage(
      refreshed ? failureMessage : `${failureMessage}；新验证码获取失败，请手动刷新。`,
    );
  } finally {
    loginState.submitting = false;
    loginElements.submit.disabled = false;
    loginElements.submit.textContent = "登录并进入工作台";
  }
}

async function initializeLogin() {
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
    setLoginMessage(error.message);
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
