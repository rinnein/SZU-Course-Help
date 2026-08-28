"use strict";

const REQUEST_TIMEOUT_MS = 30000;
const SESSION_RECOVERY_TIMEOUT_MS = 180000;
const FILTER_PAGE_SIZE = 10;
const MAX_CATALOG_PAGES = 1000;
const CATALOG_PAGE_DELAY_MS = 150;
const SEARCH_DEBOUNCE_MS = 250;
const TIMETABLE_MIN_ROW_HEIGHT = 64;
const SESSION_POLL_INTERVAL_MS = 5000;

const categoryNames = {
  TJKC: "本班推荐",
  FANKC: "方案内课程",
  FAWKC: "方案外课程",
  XGXK: "校公选课",
  TYKC: "体育课程",
  MOOC: "慕课",
  FXKC: "辅修课程",
};

const statusNames = {
  PENDING: "待启动",
  ENROLLING: "抢课中",
  SUCCESS: "已抢到",
  FAILED: "已停止",
};

const appState = {
  type: "TJKC",
  page: 1,
  totalCount: 0,
  courses: [],
  searchKeyword: "",
  searchResults: [],
  searchPage: 1,
  catalogCaches: {},
  loadingCatalog: false,
  catalogLoadingType: "",
  catalogRequestController: null,
  catalogRequestId: 0,
  cart: [],
  session: null,
  loadingCourses: false,
  courseRequestController: null,
  courseRequestId: 0,
  courseDataKey: "",
  catalogBlockedCode: "",
  loadingSession: false,
  sessionTimer: null,
  refreshingPhase: false,
  preselection: false,
  closedPhase: false,
  grabPhase: false,
  myCourses: [],
  myCoursesLoaded: false,
  loadingMyCourses: false,
  timetable: null,
  timetableFitFrame: null,
  timetableResizeTimer: null,
  switchingCampus: false,
  progress: null,
  progressTimer: null,
  loadingProgress: false,
  knownSuccessIds: new Set(),
  wasTaskRunning: false,
  taskControlPending: false,
  recoveryHideTimer: null,
  recoveryDismissedAt: "",
  lastReloginStatus: "idle",
};

const appElements = {
  categoryList: document.querySelector("#categoryList"),
  phaseBanner: document.querySelector("#phaseBanner"),
  phaseTitle: document.querySelector("#phaseTitle"),
  phaseDescription: document.querySelector("#phaseDescription"),
  phaseBadge: document.querySelector("#phaseBadge"),
  refreshPhase: document.querySelector("#refreshPhase"),
  studentLabel: document.querySelector("#studentLabel"),
  taskIndicator: document.querySelector("#taskIndicator"),
  sessionRecoveryBanner: document.querySelector("#sessionRecoveryBanner"),
  recoveryTitle: document.querySelector("#recoveryTitle"),
  recoveryDetail: document.querySelector("#recoveryDetail"),
  recoveryLoginLink: document.querySelector("#recoveryLoginLink"),
  courseTypeCode: document.querySelector("#courseTypeCode"),
  courseTitle: document.querySelector("#courseTitle"),
  courseSummary: document.querySelector("#courseSummary"),
  courseSearch: document.querySelector("#courseSearch"),
  refreshCourses: document.querySelector("#refreshCourses"),
  campusSelect: document.querySelector("#campusSelect"),
  courseList: document.querySelector("#courseList"),
  previousPage: document.querySelector("#previousPage"),
  nextPage: document.querySelector("#nextPage"),
  pageLabel: document.querySelector("#pageLabel"),
  openCart: document.querySelector("#openCart"),
  cartCount: document.querySelector("#cartCount"),
  cartDialog: document.querySelector("#cartDialog"),
  cartList: document.querySelector("#cartList"),
  cartHint: document.querySelector("#cartHint"),
  openEnrollConfirm: document.querySelector("#openEnrollConfirm"),
  enrollDialog: document.querySelector("#enrollDialog"),
  phaseConfirmation: document.querySelector("#phaseConfirmation"),
  startEnroll: document.querySelector("#startEnroll"),
  enrollMessage: document.querySelector("#enrollMessage"),
  sessionDialog: document.querySelector("#sessionDialog"),
  sessionMessage: document.querySelector("#sessionMessage"),
  sessionLoginLink: document.querySelector("#sessionDialog a[href^='/login']"),
  brandLink: document.querySelector(".topbar .brand-lockup"),
  logout: document.querySelector("#logoutButton"),
  openSchoolRaw: document.querySelector("#openSchoolRaw"),
  toastRegion: document.querySelector("#toastRegion"),
  openMyCourses: document.querySelector("#openMyCourses"),
  myCoursesDialog: document.querySelector("#myCoursesDialog"),
  myCoursesList: document.querySelector("#myCoursesList"),
  myCoursesHint: document.querySelector("#myCoursesHint"),
  refreshMyCourses: document.querySelector("#refreshMyCourses"),
  openTimetable: document.querySelector("#openTimetable"),
  timetableDialog: document.querySelector("#timetableDialog"),
  timetableContent: document.querySelector("#timetableContent"),
  timetableSummary: document.querySelector("#timetableSummary"),
  timetableHint: document.querySelector("#timetableHint"),
  refreshTimetable: document.querySelector("#refreshTimetable"),
  enrollProgress: document.querySelector("#enrollProgress"),
  progressCounts: document.querySelector("#progressCounts"),
  progressBarFill: document.querySelector("#progressBarFill"),
  progressRows: document.querySelector("#progressRows"),
  progressState: document.querySelector("#progressState"),
  progressNotice: document.querySelector("#progressNotice"),
  taskControlButton: document.querySelector("#taskControlButton"),
};

class ApiError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "ApiError";
    this.status = Number(options.status || 0);
    this.code = options.code || "REQUEST_FAILED";
    this.retryable = Boolean(options.retryable);
    this.requiresManualLogin = Boolean(options.requiresManualLogin);
    this.payload = options.payload || {};
  }
}

class SessionExpiredError extends ApiError {
  constructor(message, options = {}) {
    super(message, options);
    this.name = "SessionExpiredError";
  }
}

function versionedPage(path) {
  const queryToken = new URLSearchParams(window.location.search).get("ui") || "";
  const token = queryToken || appState.session?.ui_cache_token || "";
  if (!token) return path;
  const url = new URL(path, window.location.origin);
  url.searchParams.set("ui", token);
  return `${url.pathname}${url.search}`;
}

function isAbortError(error) {
  return error instanceof DOMException && error.name === "AbortError";
}

function catalogScopeKey(session = appState.session) {
  if (!session?.logged_in) return "";
  return JSON.stringify([
    String(session.student_id || ""),
    String(session.batch_code || ""),
    String(session.batch_name || ""),
    String(session.campus_code || "01"),
  ]);
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

function campusOptions(session = appState.session) {
  return Array.isArray(session?.campus_options)
    ? session.campus_options.filter((item) => item?.code && item?.name)
    : [];
}

function syncCampusControl() {
  const options = campusOptions();
  appElements.campusSelect.disabled = appState.switchingCampus
    || !appState.session?.logged_in
    || Boolean(appState.session?.relogin_in_progress)
    || options.length < 2;
}

function renderCampusOptions(session = appState.session) {
  const options = campusOptions(session);
  const selectedCode = String(session?.campus_code || "01");
  const signature = JSON.stringify([
    selectedCode,
    options.map((item) => [String(item.code), String(item.name)]),
  ]);
  if (appElements.campusSelect.dataset.signature === signature) {
    appElements.campusSelect.value = selectedCode;
    syncCampusControl();
    return;
  }
  const fragment = document.createDocumentFragment();
  if (!options.length) {
    const option = element(
      "option",
      "",
      session?.campus_name || "重启程序后可切换校区",
    );
    option.value = selectedCode;
    fragment.append(option);
  } else {
    for (const campus of options) {
      const option = element("option", "", campus.name);
      option.value = String(campus.code);
      fragment.append(option);
    }
  }
  appElements.campusSelect.replaceChildren(fragment);
  appElements.campusSelect.dataset.signature = signature;
  appElements.campusSelect.value = selectedCode;
  syncCampusControl();
}

async function switchCampus(nextCode) {
  const normalizedCode = String(nextCode || "").trim();
  const previousCode = String(appState.session?.campus_code || "01");
  if (!normalizedCode || normalizedCode === previousCode || appState.switchingCampus) return;

  appState.switchingCampus = true;
  syncCampusControl();
  appState.courseRequestId += 1;
  appState.courseRequestController?.abort();
  appState.courseRequestController = null;
  abortCatalogFetch();
  try {
    const session = await api("/api/session/campus", {
      method: "POST",
      body: JSON.stringify({ campus_code: normalizedCode }),
    });
    appState.page = 1;
    appState.searchKeyword = "";
    appState.searchResults = [];
    appState.searchPage = 1;
    appState.catalogBlockedCode = "";
    appElements.courseSearch.value = "";
    applySessionData(session);
    showToast(session.message || `已切换到${session.campus_name || "新校区"}`, false, true);
    if (courseCatalogBlocked()) renderCourseAvailabilityState();
    else await loadCourses();
  } catch (error) {
    appElements.campusSelect.value = previousCode;
    if (!(error instanceof SessionExpiredError)) showToast(`校区切换失败：${error.message}`, true);
  } finally {
    appState.switchingCampus = false;
    syncCampusControl();
  }
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

async function api(url, options = {}) {
  const upstreamSignal = options.signal;
  const timeoutMs = Number(options.timeoutMs || REQUEST_TIMEOUT_MS);
  const controller = new AbortController();
  const abortFromUpstream = () => controller.abort();
  if (upstreamSignal?.aborted) controller.abort();
  upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  const {
    signal: _ignoredSignal,
    timeoutMs: _ignoredTimeout,
    ...fetchOptions
  } = options;

  try {
    const response = await fetch(url, {
      cache: "no-store",
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const data = await readJson(response);
    if (response.status === 401) {
      const message = data.message || "登录已过期，请重新登录";
      showSessionDialog(message);
      throw new SessionExpiredError(message, {
        status: response.status,
        code: data.error_code || "NOT_LOGGED_IN",
        retryable: data.retryable,
        requiresManualLogin: data.requires_manual_login,
        payload: data,
      });
    }
    if (!response.ok) {
      throw new ApiError(data.message || data.detail || "请求失败，请稍后重试", {
        status: response.status,
        code: data.error_code,
        retryable: data.retryable,
        requiresManualLogin: data.requires_manual_login,
        payload: data,
      });
    }
    return data;
  } catch (error) {
    if (controller.signal.aborted && !upstreamSignal?.aborted) {
      throw new ApiError("请求超时，请检查网络后重试", {
        code: "REQUEST_TIMEOUT",
        retryable: true,
      });
    }
    if (error instanceof ApiError || isAbortError(error)) throw error;
    if (error instanceof TypeError) {
      throw new ApiError("无法连接本地服务，请确认程序仍在运行", {
        code: "LOCAL_SERVICE_UNAVAILABLE",
        retryable: true,
      });
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}

function showToast(message, error = false, success = false) {
  const variant = error ? " is-error" : success ? " is-success" : "";
  const toast = element("div", `toast${variant}`, message);
  appElements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), success ? 4600 : 3600);
}

function showSessionDialog(message) {
  appElements.sessionMessage.textContent = message;
  appElements.sessionLoginLink.href = versionedPage("/login");
  if (!appElements.sessionDialog.open) appElements.sessionDialog.showModal();
}

function hideSessionDialog() {
  if (appElements.sessionDialog.open) appElements.sessionDialog.close();
}

function hideRecoveryBanner() {
  appElements.sessionRecoveryBanner.hidden = true;
  appElements.sessionRecoveryBanner.setAttribute("aria-busy", "false");
}

function renderSessionRecovery(session, previousStatus = "idle") {
  const status = String(session?.relogin_status || "idle");
  const taskPaused = Boolean(session?.task_paused);
  const taskRunning = Boolean(session?.task_running);
  const finishedAt = String(session?.relogin_finished_at || "success");
  appElements.recoveryLoginLink.href = versionedPage("/login");

  if (status === "idle") {
    hideRecoveryBanner();
    return;
  }

  if (status === "running") {
    if (appState.recoveryHideTimer) {
      window.clearTimeout(appState.recoveryHideTimer);
      appState.recoveryHideTimer = null;
    }
    appElements.sessionRecoveryBanner.hidden = false;
    appElements.sessionRecoveryBanner.className = "session-recovery-banner is-running";
    appElements.sessionRecoveryBanner.setAttribute("aria-busy", "true");
    appElements.recoveryTitle.textContent = "正在自动重新登录";
    appElements.recoveryDetail.textContent = session.relogin_message
      || `学校会话已过期，OCR 最多尝试 ${session.relogin_max_attempts || 50} 张验证码；任务和清单会保留。`;
    appElements.recoveryLoginLink.hidden = true;
    return;
  }

  appElements.sessionRecoveryBanner.setAttribute("aria-busy", "false");
  if (status === "success") {
    hideSessionDialog();
    if (appState.recoveryDismissedAt === finishedAt) {
      hideRecoveryBanner();
      return;
    }
    appElements.sessionRecoveryBanner.hidden = false;
    appElements.sessionRecoveryBanner.className = "session-recovery-banner is-success";
    appElements.recoveryTitle.textContent = "自动重新登录成功";
    appElements.recoveryDetail.textContent = "学校会话已经恢复，页面数据与抢课任务会自动继续。";
    appElements.recoveryLoginLink.hidden = true;
    if (previousStatus === "running") {
      showToast("自动重新登录成功，已恢复学校会话", false, true);
    }
    if (!appState.recoveryHideTimer) {
      appState.recoveryHideTimer = window.setTimeout(() => {
        appState.recoveryDismissedAt = finishedAt;
        appState.recoveryHideTimer = null;
        hideRecoveryBanner();
      }, 6000);
    }
    return;
  }

  if (appState.recoveryHideTimer) {
    window.clearTimeout(appState.recoveryHideTimer);
    appState.recoveryHideTimer = null;
  }
  appElements.sessionRecoveryBanner.hidden = false;
  appElements.sessionRecoveryBanner.className = "session-recovery-banner is-error";
  appElements.recoveryTitle.textContent = taskRunning && !taskPaused
    ? "自动重新登录暂未成功"
    : "自动重新登录失败";
  appElements.recoveryDetail.textContent = taskRunning && !taskPaused
    ? `${session.relogin_message || "OCR 暂未识别成功"}；后台仍会按策略继续尝试。`
    : `${session.relogin_message || "无法恢复学校会话"}；请手动登录后返回清单继续任务。`;
  appElements.recoveryLoginLink.hidden = taskRunning && !taskPaused;
}

function renderLoading() {
  const wrapper = element("div", "loading-list");
  for (let index = 0; index < 4; index += 1) {
    wrapper.append(element("div", "loading-row"));
  }
  appElements.courseList.replaceChildren(wrapper);
}

function renderState(title, message, options = {}) {
  const wrapper = element("div", options.tone === "error" ? "error-state" : "empty-state");
  wrapper.append(element("strong", "", title));
  wrapper.append(element("p", "", message));
  if (options.note) wrapper.append(element("span", "state-note", options.note));
  if (Array.isArray(options.actions) && options.actions.length) {
    const actions = element("div", "state-actions");
    for (const action of options.actions) {
      const button = element(
        "button",
        action.primary ? "button button-primary" : "button button-secondary",
        action.label,
      );
      button.type = "button";
      button.addEventListener("click", action.handler);
      actions.append(button);
    }
    wrapper.append(actions);
  }
  appElements.courseList.replaceChildren(wrapper);
}

function courseCatalogBlocked() {
  return Boolean(
    appState.closedPhase
      || !appState.session?.batch_code
      || ["COURSE_WINDOW_CLOSED", "BATCH_UNAVAILABLE"].includes(appState.catalogBlockedCode),
  );
}

function renderCourseAvailabilityState(message = "") {
  appState.courses = [];
  appState.totalCount = 0;
  appState.courseDataKey = "";
  appState.searchKeyword = "";
  appState.searchResults = [];
  appState.searchPage = 1;
  appElements.courseSearch.value = "";
  invalidateCatalogCaches();
  appElements.courseTypeCode.textContent = appState.type;
  appElements.courseTitle.textContent = categoryNames[appState.type] || appState.type;
  appElements.courseSearch.disabled = true;
  const closed = appState.closedPhase || appState.catalogBlockedCode === "COURSE_WINDOW_CLOSED";
  appElements.courseSummary.textContent = closed
    ? "课程目录当前不可用，本地清单已保留"
    : "等待学校返回有效选课批次";
  updatePagination();

  if (closed) {
    renderState(
      "当前未开放课程目录",
      message || "你已经登录，但学校当前批次显示为未开放、暂停或已结束。课程目录暂时不可读取。",
      {
        note: "已加入的本地选课清单不会丢失。开放后重新检查状态即可继续浏览。",
        actions: [{ label: "重新检查开放状态", handler: refreshPhaseAndCourses, primary: true }],
      },
    );
    return;
  }

  renderState(
    "暂未读取到选课批次",
    message || "登录已经成功，但学校当前没有返回可用的选课批次，可能尚未开放或服务正在波动。",
    {
      note: "这不代表登录失败，也不会影响本地选课清单。",
      actions: [{ label: "重新检查开放状态", handler: refreshPhaseAndCourses, primary: true }],
    },
  );
}

function setPhasePresentation() {
  const batch = (appState.session?.batch_name || "").trim();
  const catalogReportedClosed = appState.catalogBlockedCode === "COURSE_WINDOW_CLOSED";
  const taskWindowPaused = Boolean(
    appState.session?.task_paused && appState.session?.task_pause_source === "school_window",
  );
  const taskPauseAcknowledged = Boolean(appState.session?.task_pause_acknowledged);
  appState.preselection = appState.session?.phase === "preselection" && !catalogReportedClosed;
  appState.closedPhase = appState.session?.phase === "closed" || catalogReportedClosed;
  appState.grabPhase = Boolean(appState.session?.automatic_enroll_allowed);
  appElements.phaseBanner.classList.remove("is-warning", "is-danger");
  appElements.phaseBadge.className = "status-pill status-neutral";

  if (appState.preselection) {
    appElements.phaseBanner.classList.add("is-danger");
    appElements.phaseTitle.textContent = "当前为预选阶段";
    appElements.phaseDescription.textContent = "可以浏览课程并整理清单，后端已禁止启动自动抢课。";
    appElements.phaseBadge.textContent = batch || "预选";
    appElements.phaseBadge.className = "status-pill status-danger";
  } else if (appState.closedPhase) {
    appElements.phaseBanner.classList.add("is-warning");
    appElements.phaseTitle.textContent = "当前不在开放选课时间";
    appElements.phaseDescription.textContent = "学校批次显示为未开放、暂停或已结束；本地清单会保留。";
    appElements.phaseBadge.textContent = batch || "未开放";
    appElements.phaseBadge.className = "status-pill status-warning";
  } else if (taskWindowPaused) {
    appElements.phaseBanner.classList.add("is-warning");
    appElements.phaseTitle.textContent = `批次为${batch || "可抢阶段"}，但当前时段未开放`;
    appElements.phaseDescription.textContent = appState.session?.task_pause_reason
      || "学校拒绝了本次提交，任务已暂停；开放后可在清单中继续。";
    appElements.phaseBadge.textContent = taskPauseAcknowledged ? "任务已暂停" : "正在暂停";
    appElements.phaseBadge.className = "status-pill status-warning";
  } else if (appState.grabPhase) {
    appElements.phaseTitle.textContent = `当前批次：${batch}`;
    appElements.phaseDescription.textContent = "启动前仍需在清单中再次确认阶段。抢到的课程会实时加入你的课程。";
    appElements.phaseBadge.textContent = batch;
    appElements.phaseBadge.className = "status-pill status-success";
  } else {
    appElements.phaseBanner.classList.add("is-warning");
    appElements.phaseTitle.textContent = batch ? `当前批次：${batch}` : "暂未读取到选课批次";
    appElements.phaseDescription.textContent = "该批次不在自动抢课白名单内，只能浏览和整理课程。";
    appElements.phaseBadge.textContent = batch || "未知批次";
    appElements.phaseBadge.className = "status-pill status-warning";
  }
  syncEnrollControls();
}

function applySessionData(session) {
  const previousReloginStatus = appState.lastReloginStatus;
  const previousCatalogScope = catalogScopeKey(appState.session);
  const nextCatalogScope = catalogScopeKey(session);
  const reloginCompleted = Boolean(appState.session)
    && String(session.relogin_status || "idle") === "success"
    && previousReloginStatus !== "success";
  const catalogContextChanged = Boolean(
    (previousCatalogScope && previousCatalogScope !== nextCatalogScope)
    || reloginCompleted
  );
  if (catalogContextChanged) {
    invalidateCatalogCaches();
    appState.courses = [];
    appState.totalCount = 0;
    appState.courseDataKey = "";
  }
  appState.session = session;
  renderCampusOptions(session);
  appState.lastReloginStatus = String(session.relogin_status || "idle");
  appElements.studentLabel.textContent = session.relogin_in_progress
    ? "正在恢复登录"
    : session.logged_in
      ? `学号 ${session.student_id}`
      : "未登录";
  renderSessionRecovery(session, previousReloginStatus);
  if (session.logged_in && !session.relogin_in_progress) hideSessionDialog();
  updateTaskIndicator();
  setPhasePresentation();
  if (session.task_running) startProgressPolling();
  return catalogContextChanged;
}

function cartEditStateKey() {
  const session = appState.session;
  return [
    Boolean(session?.task_running),
    Boolean(session?.task_paused),
    Boolean(session?.task_pause_acknowledged),
    Boolean(session?.task_stopping),
  ].join(":");
}

function applyProgressTaskState(data) {
  const previousCartEditState = cartEditStateKey();
  appState.progress = data;
  if (appState.session && data) {
    appState.session.task_running = Boolean(data.running);
    appState.session.task_paused = Boolean(data.paused);
    appState.session.task_pause_acknowledged = Boolean(data.pause_acknowledged);
    appState.session.task_pause_reason = data.pause_reason || "";
    appState.session.task_pause_source = data.pause_source || "";
    appState.session.task_stopping = Boolean(data.stopping);
    appState.session.task_stopping_reason = data.stopping_reason || "";
  }
  return previousCartEditState !== cartEditStateKey();
}

function updateTaskIndicator() {
  const running = Boolean(appState.progress?.running ?? appState.session?.task_running);
  const paused = Boolean(appState.progress?.paused ?? appState.session?.task_paused);
  const pauseAcknowledged = Boolean(
    appState.progress?.pause_acknowledged ?? appState.session?.task_pause_acknowledged,
  );
  const stopping = Boolean(appState.progress?.stopping ?? appState.session?.task_stopping);
  const relogin = Boolean(appState.session?.relogin_in_progress);
  appElements.taskIndicator.hidden = !running;
  if (!running) return;
  appElements.taskIndicator.classList.toggle("is-paused", paused);
  appElements.taskIndicator.classList.toggle("is-relogin", relogin);
  appElements.taskIndicator.classList.toggle("is-stopping", stopping);
  if (stopping) {
    appElements.taskIndicator.textContent = "任务正在结束";
    return;
  }
  if (relogin) {
    appElements.taskIndicator.textContent = "正在重新登录";
    return;
  }
  if (paused) {
    appElements.taskIndicator.textContent = pauseAcknowledged ? "任务已暂停" : "正在暂停";
    return;
  }
  const counts = appState.progress?.counts;
  appElements.taskIndicator.textContent = counts
    ? `抢课中 ${counts.success}/${counts.total}`
    : "抢课中";
}

async function loadSession(showDialog = true, refreshOnCatalogChange = true) {
  if (appState.loadingSession) return appState.session;
  appState.loadingSession = true;
  const previousTaskState = Boolean(appState.session?.task_running);
  try {
    const session = await api("/api/session");
    const catalogContextChanged = applySessionData(session);
    if (
      !appState.session.logged_in
      && !appState.session.relogin_in_progress
      && appState.session.relogin_status !== "failed"
      && showDialog
    ) {
      showSessionDialog("当前没有有效登录状态，请返回登录页完成登录。");
    }
    if (previousTaskState !== Boolean(appState.session.task_running)) {
      await loadCart();
    }
    if (catalogContextChanged) {
      if (!appState.session.logged_in) {
        appState.searchKeyword = "";
        appElements.courseSearch.value = "";
        appElements.courseSearch.disabled = true;
        renderState("登录状态已失效", "请返回登录页完成登录后再读取课程目录。");
        updatePagination();
      } else if (courseCatalogBlocked()) {
        renderCourseAvailabilityState();
      } else if (refreshOnCatalogChange) {
        if (!appState.session.task_running) {
          await refreshCurrentView();
        } else {
          appElements.courseSummary.textContent = "课程状态需要重新加载";
          renderState(
            "登录状态或选课批次已更新",
            "抢课任务仍在运行；为避免额外占用学校接口，请在需要时手动刷新课程。",
            {
              actions: [
                { label: "重新加载课程", handler: refreshCurrentView, primary: true },
              ],
            },
          );
          updatePagination();
        }
      }
    }
    return appState.session;
  } catch (error) {
    if (!(error instanceof SessionExpiredError)) showToast(error.message, true);
    return appState.session;
  } finally {
    appState.loadingSession = false;
  }
}

function classTag(classInfo) {
  if (String(classInfo.is_choose) === "1") return ["已选", "tag-chosen"];
  if (String(classInfo.is_conflict) === "1") return ["时间冲突", "tag-conflict"];
  if (String(classInfo.is_full) === "1") return ["已满", "tag-full"];
  return ["可加入", "tag-open"];
}

function appendClassRow(container, course, classInfo) {
  const row = element("div", "class-row");
  const primary = element("div", "class-primary");
  primary.append(element("strong", "", classInfo.teacher_name || "教师待定"));
  primary.append(element("span", "", classInfo.course_index || classInfo.teaching_class_id));

  const location = element("div", "class-location");
  location.append(element("strong", "", classInfo.teaching_place || "时间地点待定"));
  location.append(element("span", "", classInfo.sport_name || (String(classInfo.is_mooc) === "1" ? "线上课程" : "教学安排")));

  const selected = classInfo.number_of_selected || classInfo.course_total_number || "-";
  const capacityValue = classInfo.class_capacity || "-";
  const capacity = element("div", "class-capacity");
  capacity.append(element("strong", "", `${selected} / ${capacityValue}`));
  capacity.append(element("span", "", "已选 / 容量"));

  const actions = element("div", "class-actions");
  const [tagText, tagClass] = classTag(classInfo);
  actions.append(element("span", `class-tag ${tagClass}`, tagText));

  const blocked = String(classInfo.is_choose) === "1" || String(classInfo.is_conflict) === "1";
  const addButton = element(
    "button",
    "button button-secondary",
    blocked ? (String(classInfo.is_choose) === "1" ? "已选" : "不可加入") : (String(classInfo.is_full) === "1" ? "加入候补" : "加入清单"),
  );
  addButton.type = "button";
  addButton.disabled = blocked || Boolean(appState.session?.task_running);
  addButton.addEventListener("click", async () => {
    addButton.disabled = true;
    try {
      const result = await api("/api/courses/add", {
        method: "POST",
        body: JSON.stringify({
          id: String(classInfo.teaching_class_id || ""),
          type: appState.type,
          name: `${course.course_name || "未命名课程"} (${classInfo.teacher_name || "教师待定"})`,
          campus_code: String(appState.session?.campus_code || "01"),
          campus_name: String(appState.session?.campus_name || course.campus_name || ""),
          is_choose: String(classInfo.is_choose || ""),
          is_conflict: String(classInfo.is_conflict || ""),
          is_full: String(classInfo.is_full || ""),
        }),
      });
      if (result.is_error) throw new Error(result.message);
      showToast(result.message || "已加入选课清单");
      await loadCart();
    } catch (error) {
      if (!(error instanceof SessionExpiredError)) showToast(error.message, true);
    } finally {
      addButton.disabled = blocked || Boolean(appState.session?.task_running);
    }
  });
  actions.append(addButton);
  row.append(primary, location, capacity, actions);
  container.append(row);
}

function isFilterActive() {
  return appState.searchKeyword.length > 0;
}

function courseMatchesKeyword(course, keyword) {
  const teachingText = (course.tcList || [])
    .map((item) => `${item.teacher_name || ""} ${item.teaching_place || ""}`)
    .join(" ");
  return `${course.course_name || ""} ${course.course_number || ""} ${course.department_name || ""} ${teachingText}`
    .toLowerCase()
    .includes(keyword);
}

function renderCourseList(courses) {
  const fragment = document.createDocumentFragment();
  courses.forEach((course, index) => {
    const details = element("details", "course-group");
    details.style.animationDelay = `${Math.min(index, 8) * 40}ms`;
    const summary = element("summary");
    const main = element("div", "course-summary-main");
    main.append(element("strong", "", course.course_name || "未命名课程"));
    main.append(
      element(
        "span",
        "",
        [course.course_number, course.department_name, course.campus_name, course.sport_name]
          .filter(Boolean)
          .join(" · ") || "课程信息待定",
      ),
    );
    const side = element("div", "course-summary-side");
    side.append(element("span", "", `${(course.tcList || []).length} 个教学班`));
    side.append(element("i", "course-chevron"));
    summary.append(main, side);

    const classes = element("div", "class-list");
    if (!(course.tcList || []).length) {
      classes.append(element("div", "empty-state", "暂无教学班"));
    } else {
      for (const classInfo of course.tcList) appendClassRow(classes, course, classInfo);
    }
    details.append(summary, classes);
    fragment.append(details);
  });
  appElements.courseList.replaceChildren(fragment);
}

function applyCourseFilter() {
  const cache = appState.catalogCaches[appState.type];
  if (!cache || !cache.complete || cache.scopeKey !== catalogScopeKey()) return;
  const keyword = appState.searchKeyword.toLowerCase();
  const results = keyword
    ? cache.courses.filter((course) => courseMatchesKeyword(course, keyword))
    : [];
  appState.searchResults = results;
  const totalPages = Math.max(1, Math.ceil(results.length / FILTER_PAGE_SIZE));
  if (appState.searchPage > totalPages) appState.searchPage = totalPages;
  const pageItems = results.slice(
    (appState.searchPage - 1) * FILTER_PAGE_SIZE,
    appState.searchPage * FILTER_PAGE_SIZE,
  );
  appElements.courseSummary.textContent = results.length
    ? `匹配 ${results.length} 门课程（全部 ${cache.totalCount} 门），本页 ${pageItems.length} 门`
    : `没有匹配课程，全部目录共 ${cache.totalCount} 门`;
}

function renderFilteredCourses() {
  const cache = appState.catalogCaches[appState.type];
  if (!cache || !cache.complete || cache.scopeKey !== catalogScopeKey()) {
    if (appState.loadingCatalog && appState.catalogLoadingType === appState.type) {
      renderState(
        "正在加载全部课程",
        appElements.courseSummary.textContent || "正在读取学校课程数据，加载完成后即可搜索整个目录。",
      );
    } else {
      renderState("课程目录尚未加载", "暂时无法在全部课程中搜索，请重新加载后重试。", {
        tone: "error",
        actions: [
          { label: "重新加载课程", handler: () => runSearchFetch({ force: true }), primary: true },
        ],
      });
    }
    updatePagination();
    return;
  }

  const results = Array.isArray(appState.searchResults) ? appState.searchResults : [];
  if (!results.length) {
    renderState("没有匹配的课程", "换一个关键词，或切换课程目录后重试。");
    updatePagination();
    return;
  }

  const totalPages = Math.max(1, Math.ceil(results.length / FILTER_PAGE_SIZE));
  if (appState.searchPage > totalPages) appState.searchPage = totalPages;
  const start = (appState.searchPage - 1) * FILTER_PAGE_SIZE;
  renderCourseList(results.slice(start, start + FILTER_PAGE_SIZE));
  updatePagination();
}

function renderCourses() {
  if (isFilterActive()) {
    renderFilteredCourses();
    return;
  }
  if (!appState.courses.length) {
    renderState("本页没有课程", "学校系统当前没有返回该目录的课程。");
    return;
  }
  renderCourseList(appState.courses);
}

function updatePagination() {
  const blocked = courseCatalogBlocked();
  if (isFilterActive()) {
    const results = Array.isArray(appState.searchResults) ? appState.searchResults : [];
    const totalPages = Math.max(1, Math.ceil(results.length / FILTER_PAGE_SIZE));
    appElements.pageLabel.textContent = `第 ${appState.searchPage} / ${totalPages} 页`;
    const busy = appState.loadingCourses || appState.loadingCatalog;
    appElements.previousPage.disabled = blocked || busy || appState.searchPage <= 1;
    appElements.nextPage.disabled = blocked || busy || appState.searchPage >= totalPages;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(appState.totalCount / FILTER_PAGE_SIZE));
  appElements.pageLabel.textContent = `第 ${appState.page} / ${totalPages} 页`;
  appElements.previousPage.disabled = blocked || appState.page <= 1 || appState.loadingCourses;
  appElements.nextPage.disabled = blocked || appState.page >= totalPages || appState.loadingCourses;
}

function abortCatalogFetch() {
  const loadingType = appState.catalogLoadingType;
  appState.catalogRequestId += 1;
  appState.catalogRequestController?.abort();
  appState.catalogRequestController = null;
  appState.loadingCatalog = false;
  appState.catalogLoadingType = "";
  if (loadingType && !appState.catalogCaches[loadingType]?.complete) {
    delete appState.catalogCaches[loadingType];
  }
  appElements.refreshCourses.disabled = appState.loadingCourses || appState.refreshingPhase;
}

function invalidateCatalogCache(type = appState.type) {
  const normalizedType = String(type || "");
  if (!normalizedType) return;
  if (appState.catalogLoadingType === normalizedType) abortCatalogFetch();
  delete appState.catalogCaches[normalizedType];
  if (normalizedType === appState.type) {
    appState.searchResults = [];
    appState.searchPage = 1;
  }
}

function invalidateCatalogCaches() {
  abortCatalogFetch();
  appState.catalogCaches = {};
  appState.searchResults = [];
  appState.searchPage = 1;
}

function waitForCatalogPageDelay(signal) {
  if (CATALOG_PAGE_DELAY_MS <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Catalog request aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Catalog request aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, CATALOG_PAGE_DELAY_MS);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function fetchFullCatalog({ force = false } = {}) {
  const type = appState.type;
  const scopeKey = catalogScopeKey();
  const existing = appState.catalogCaches[type];
  if (existing?.complete && existing.scopeKey === scopeKey && !force) return true;
  if (appState.loadingCatalog && appState.catalogLoadingType === type) return true;

  abortCatalogFetch();
  const controller = new AbortController();
  const requestId = appState.catalogRequestId + 1;
  appState.catalogRequestId = requestId;
  appState.catalogRequestController = controller;
  appState.loadingCatalog = true;
  appState.catalogLoadingType = type;
  appElements.refreshCourses.disabled = true;
  const cache = {
    courses: [],
    totalCount: 0,
    complete: false,
    scopeKey,
  };
  appState.catalogCaches[type] = cache;

  const updateProgress = () => {
    appElements.courseSummary.textContent = cache.totalCount
      ? `正在加载全部课程 ${cache.courses.length} / ${cache.totalCount} 门`
      : "正在加载全部课程";
  };
  updateProgress();
  renderCourses();
  updatePagination();

  try {
    let completed = false;
    let expectedTotalCount = null;
    for (let page = 1; page <= MAX_CATALOG_PAGES; page += 1) {
      const data = await api(
        `/api/school/courses?type=${encodeURIComponent(type)}&page=${page}&page_size=${FILTER_PAGE_SIZE}`,
        { signal: controller.signal, timeoutMs: SESSION_RECOVERY_TIMEOUT_MS },
      );
      if (
        requestId !== appState.catalogRequestId
        || scopeKey !== catalogScopeKey()
      ) return false;
      const items = Array.isArray(data.courses) ? data.courses : [];
      const reportedTotal = Number(data.total_count);
      if (!Number.isInteger(reportedTotal) || reportedTotal < 0) {
        throw new ApiError("学校返回的课程总数无效，请稍后重新加载", {
          code: "SCHOOL_RESPONSE_INVALID",
          retryable: true,
        });
      }
      if (expectedTotalCount === null) {
        expectedTotalCount = reportedTotal;
      } else if (reportedTotal !== expectedTotalCount) {
        throw new ApiError("课程总数在分页读取期间发生变化，请重新加载", {
          code: "SCHOOL_RESPONSE_INVALID",
          retryable: true,
        });
      }
      cache.totalCount = expectedTotalCount;
      cache.courses.push(...items);
      updateProgress();
      const totalPages = Math.max(1, Math.ceil(cache.totalCount / FILTER_PAGE_SIZE));
      if (totalPages > MAX_CATALOG_PAGES) {
        throw new ApiError(
          `该课程目录共有 ${totalPages} 页，超过安全加载上限 ${MAX_CATALOG_PAGES} 页`,
          { code: "CATALOG_PAGE_LIMIT", retryable: false },
        );
      }
      if (page >= totalPages) {
        if (cache.courses.length !== cache.totalCount) {
          throw new ApiError("学校课程目录分页数据不完整，请稍后重新加载", {
            code: "SCHOOL_RESPONSE_INVALID",
            retryable: true,
          });
        }
        completed = true;
        break;
      }
      if (!items.length) {
        throw new ApiError("学校课程目录中途返回了空页，请稍后重新加载", {
          code: "SCHOOL_RESPONSE_INVALID",
          retryable: true,
        });
      }
      await waitForCatalogPageDelay(controller.signal);
    }
    if (requestId !== appState.catalogRequestId) return false;
    if (!completed) {
      throw new ApiError("课程目录未能在安全页数范围内加载完成", {
        code: "CATALOG_PAGE_LIMIT",
        retryable: false,
      });
    }
    cache.complete = true;
    return true;
  } catch (error) {
    if (isAbortError(error) || requestId !== appState.catalogRequestId) return false;
    delete appState.catalogCaches[type];
    if (error instanceof SessionExpiredError) return false;
    const availabilityError = ["COURSE_WINDOW_CLOSED", "BATCH_UNAVAILABLE"].includes(error.code);
    if (availabilityError) {
      appState.catalogBlockedCode = error.code;
      setPhasePresentation();
      renderCourseAvailabilityState(error.message);
    } else {
      showToast(`${courseErrorTitle(error)}：${error.message}`, true);
      renderCourses();
      updatePagination();
    }
    return false;
  } finally {
    if (requestId === appState.catalogRequestId) {
      appState.loadingCatalog = false;
      appState.catalogLoadingType = "";
      appState.catalogRequestController = null;
      appElements.courseSearch.disabled = courseCatalogBlocked();
      appElements.refreshCourses.disabled = appState.loadingCourses || appState.refreshingPhase;
    }
  }
}

async function runSearchFetch({ force = false } = {}) {
  if (!appState.session?.logged_in || courseCatalogBlocked()) return;
  const ok = await fetchFullCatalog({ force });
  if (!ok || !isFilterActive()) return;
  applyCourseFilter();
  renderCourses();
  updatePagination();
}

async function handleSearchInput() {
  const keyword = appElements.courseSearch.value.trim();
  appState.searchPage = 1;
  if (!keyword) {
    if (isFilterActive()) {
      appState.searchKeyword = "";
      abortCatalogFetch();
      if (appState.courseDataKey) {
        appElements.courseSummary.textContent = `共 ${appState.totalCount} 门课程，本页 ${appState.courses.length} 门`;
        renderCourses();
      }
      updatePagination();
    }
    return;
  }
  appState.searchKeyword = keyword;
  await runSearchFetch();
}

function courseErrorTitle(error) {
  if (["SCHOOL_TIMEOUT", "REQUEST_TIMEOUT"].includes(error.code)) return "学校响应超时";
  if (["SCHOOL_NETWORK_ERROR", "LOCAL_SERVICE_UNAVAILABLE"].includes(error.code)) {
    return "暂时无法连接服务";
  }
  if (error.code === "SCHOOL_RESPONSE_INVALID") return "学校数据暂时异常";
  if (error.code === "SCHOOL_COURSE_REJECTED") return "学校暂时拒绝了请求";
  if (error.code === "UNSUPPORTED_COURSE_TYPE") return "该目录暂不支持";
  return "课程目录读取失败";
}

async function loadCourses(options = {}) {
  if (!appState.session?.logged_in) {
    renderState("尚未登录", "返回登录页完成学号、密码、卡密和验证码校验。");
    return;
  }
  if (courseCatalogBlocked()) {
    renderCourseAvailabilityState();
    return;
  }
  // 筛选模式下课程视图由全目录搜索负责，服务端分页加载直接跳过。
  if (isFilterActive()) return;

  appState.courseRequestController?.abort();
  const controller = new AbortController();
  const requestId = appState.courseRequestId + 1;
  const requestedType = appState.type;
  const requestedPage = appState.page;
  const requestedKey = `${requestedType}:${requestedPage}`;
  const preserveExisting = Boolean(options.preserveExisting);
  const hasCurrentResult = appState.courseDataKey === requestedKey;
  appState.courseRequestController = controller;
  appState.courseRequestId = requestId;
  appState.loadingCourses = true;
  appElements.refreshCourses.disabled = true;
  appElements.courseSearch.disabled = false;
  appElements.courseTypeCode.textContent = appState.type;
  appElements.courseTitle.textContent = categoryNames[appState.type] || appState.type;
  appElements.courseSummary.textContent = hasCurrentResult && preserveExisting
    ? "正在刷新，当前仍显示上次成功结果"
    : "正在读取学校课程数据";
  if (!hasCurrentResult || !preserveExisting) {
    appState.courses = [];
    appState.totalCount = 0;
    appState.courseDataKey = "";
    renderLoading();
  }
  updatePagination();

  try {
    const data = await api(
      `/api/school/courses?type=${encodeURIComponent(requestedType)}&page=${requestedPage}&page_size=10`,
      { signal: controller.signal, timeoutMs: SESSION_RECOVERY_TIMEOUT_MS },
    );
    if (requestId !== appState.courseRequestId) return;
    appState.courses = Array.isArray(data.courses) ? data.courses : [];
    appState.totalCount = Number(data.total_count || 0);
    appState.courseDataKey = requestedKey;
    appState.catalogBlockedCode = "";
    appElements.courseSummary.textContent = `共 ${appState.totalCount} 门课程，本页 ${appState.courses.length} 门`;
    renderCourses();
  } catch (error) {
    if (isAbortError(error) || requestId !== appState.courseRequestId) return;
    if (error instanceof SessionExpiredError) return;

    const availabilityError = ["COURSE_WINDOW_CLOSED", "BATCH_UNAVAILABLE"].includes(error.code);
    if (availabilityError) {
      appState.catalogBlockedCode = error.code;
      setPhasePresentation();
      renderCourseAvailabilityState(error.message);
      return;
    }

    if (hasCurrentResult && preserveExisting) {
      appElements.courseSummary.textContent = "刷新失败，仍显示上次成功结果";
      showToast(`${courseErrorTitle(error)}：${error.message}`, true);
      return;
    }

    appState.courses = [];
    appState.totalCount = 0;
    appState.courseDataKey = "";
    appElements.courseSummary.textContent = "课程目录暂时不可用";
    renderState(courseErrorTitle(error), error.message, {
      tone: "error",
      note: "登录状态和已加入的本地选课清单不会因本次读取失败而丢失。",
      actions: error.retryable
        ? [
          { label: "重新加载课程", handler: () => loadCourses({ preserveExisting: true }), primary: true },
          { label: "重新检查开放状态", handler: refreshPhaseAndCourses },
        ]
        : [],
    });
  } finally {
    if (requestId === appState.courseRequestId) {
      appState.loadingCourses = false;
      appState.courseRequestController = null;
      appElements.refreshCourses.disabled = appState.refreshingPhase || appState.loadingCatalog;
      updatePagination();
    }
  }
}

async function refreshPhaseAndCourses() {
  if (appState.refreshingPhase || !appState.session?.logged_in) return;
  appState.refreshingPhase = true;
  appState.courseRequestController?.abort();
  const previousLabel = appElements.refreshPhase.textContent;
  appElements.refreshPhase.textContent = "检查中...";
  appElements.refreshPhase.disabled = true;
  appElements.refreshCourses.disabled = true;

  try {
    const session = await api("/api/session/refresh", {
      method: "POST",
      timeoutMs: SESSION_RECOVERY_TIMEOUT_MS,
    });
    appState.catalogBlockedCode = "";
    applySessionData(session);
    showToast(session.message || "开放状态已更新", false, true);
    if (courseCatalogBlocked()) renderCourseAvailabilityState();
    else {
      invalidateCatalogCache(appState.type);
      if (isFilterActive()) await runSearchFetch({ force: true });
      else await loadCourses({ preserveExisting: true });
    }
  } catch (error) {
    if (error instanceof SessionExpiredError) return;
    if (error.payload?.session) applySessionData(error.payload.session);
    if (error.code === "BATCH_UNAVAILABLE") {
      appState.catalogBlockedCode = error.code;
      renderCourseAvailabilityState(error.message);
      return;
    }

    if (appState.courseDataKey) {
      showToast(`开放状态检查失败：${error.message}`, true);
    } else {
      appElements.courseSummary.textContent = "开放状态检查失败";
      renderState("暂时无法检查开放状态", error.message, {
        tone: "error",
        note: "当前登录状态和本地选课清单不受影响。",
        actions: error.retryable
          ? [{ label: "重新检查", handler: refreshPhaseAndCourses, primary: true }]
          : [],
      });
    }
  } finally {
    appState.refreshingPhase = false;
    appElements.refreshPhase.textContent = previousLabel;
    appElements.refreshPhase.disabled = false;
    appElements.refreshCourses.disabled = appState.loadingCourses;
    updatePagination();
  }
}

async function refreshCurrentView() {
  if (courseCatalogBlocked()) await refreshPhaseAndCourses();
  else {
    invalidateCatalogCache(appState.type);
    if (isFilterActive()) await runSearchFetch({ force: true });
    else await loadCourses({ preserveExisting: true });
  }
}

function syncEnrollControls() {
  const running = Boolean(appState.session?.task_running);
  const paused = Boolean(appState.session?.task_paused);
  const pauseAcknowledged = Boolean(appState.session?.task_pause_acknowledged);
  const stopping = Boolean(appState.session?.task_stopping);
  const hasPending = appState.cart.some((item) => (item.status || "PENDING") === "PENDING");
  appElements.openEnrollConfirm.disabled = !appState.grabPhase || running || !hasPending;
  if (running && stopping) {
    appElements.cartHint.textContent = appState.session?.task_stopping_reason
      || "待处理课程已清空，后台任务正在结束。";
  } else if (running && paused && !pauseAcknowledged) {
    appElements.cartHint.textContent = "正在完成当前学校请求；安全暂停后即可移除清单中的课程。";
  } else if (running && paused) {
    appElements.cartHint.textContent = appState.session?.task_pause_reason
      || "抢课任务已暂停，可以移除不再需要的课程，继续后会保留其余进度。";
  } else if (running) {
    appElements.cartHint.textContent = "后台抢课任务正在运行，清单已锁定，抢到的课程会自动进入我的课程。";
  } else if (appState.preselection) {
    appElements.cartHint.textContent = "预选阶段由系统抽签，无需抢课；可先整理好清单，等复选或补选再启动。";
  } else if (appState.closedPhase) {
    appElements.cartHint.textContent = "当前不在开放选课时间，清单可以保留，开放后刷新批次再启动。";
  } else if (!appState.grabPhase) {
    appElements.cartHint.textContent = "当前批次不允许自动抢课，仅可浏览和整理清单。";
  } else if (!hasPending) {
    appElements.cartHint.textContent = "清单中没有待启动课程，先从课程目录加入。";
  } else {
    appElements.cartHint.textContent = "满员课程可以加入清单排队候补；冲突或已选课程不能加入。";
  }
}

function renderCart() {
  appElements.cartCount.textContent = String(appState.cart.length);
  if (!appState.cart.length) {
    const empty = element("div", "empty-state");
    empty.append(element("strong", "", "选课清单为空"));
    empty.append(element("p", "", "从课程目录展开教学班后加入清单。"));
    appElements.cartList.replaceChildren(empty);
    syncEnrollControls();
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const item of appState.cart) {
    const row = element("div", "cart-item");
    const copy = element("div");
    copy.append(element("strong", "", item.name || item.id));
    copy.append(
      element(
        "span",
        "",
        [item.type, item.campus_name, statusNames[item.status] || item.status || "待启动"]
          .filter(Boolean)
          .join(" · "),
      ),
    );
    const actions = element("div", "cart-item-actions");
    const statusClass = item.status === "SUCCESS" ? "status-success" : item.status === "FAILED" ? "status-danger" : item.status === "ENROLLING" ? "status-warning" : "status-neutral";
    actions.append(element("span", `status-pill ${statusClass}`, statusNames[item.status] || "待启动"));
    if (item.status === "FAILED" && !appState.session?.task_running) {
      const retry = element("button", "button button-secondary", "重新排队");
      retry.type = "button";
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        try {
          const result = await api(`/api/courses/retry?id=${encodeURIComponent(item.id)}`, {
            method: "POST",
          });
          if (result.is_error) throw new Error(result.message);
          showToast(result.message || "课程已重新排队", false, true);
          await loadCart();
        } catch (error) {
          if (!(error instanceof SessionExpiredError)) showToast(error.message, true);
          retry.disabled = false;
        }
      });
      actions.append(retry);
    }
    const remove = element("button", "button button-quiet", "移除");
    remove.type = "button";
    const taskRunning = Boolean(appState.session?.task_running);
    const taskStopping = Boolean(appState.session?.task_stopping);
    const canEditPausedTask = taskRunning
      && Boolean(appState.session?.task_paused)
      && Boolean(appState.session?.task_pause_acknowledged)
      && !taskStopping;
    const terminalCourse = ["SUCCESS", "FAILED"].includes(item.status);
    remove.disabled = taskRunning && (taskStopping || (!terminalCourse && !canEditPausedTask));
    if (remove.disabled) {
      remove.title = taskStopping
        ? "抢课任务正在结束"
        : appState.session?.task_paused
          ? "正在完成当前请求，请等待安全暂停"
          : "请先暂停抢课任务";
    }
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        const result = await api(`/api/courses/delete?id=${encodeURIComponent(item.id)}`, { method: "POST" });
        if (result.is_error) throw new Error(result.message);
        showToast(result.message || "已移除");
        if (result.progress) {
          const cartControlsChanged = applyProgressTaskState(result.progress);
          renderProgress(result.progress);
          updateTaskIndicator();
          if (cartControlsChanged) renderCart();
        }
        await loadCart();
      } catch (error) {
        if (!(error instanceof SessionExpiredError)) showToast(error.message, true);
        remove.disabled = false;
      }
    });
    actions.append(remove);
    row.append(copy, actions);
    fragment.append(row);
  }
  appElements.cartList.replaceChildren(fragment);
  syncEnrollControls();
}

async function loadCart() {
  try {
    const data = await api("/api/courses/dblist?status=");
    appState.cart = Array.isArray(data) ? data : [];
    renderCart();
  } catch (error) {
    if (!(error instanceof SessionExpiredError)) showToast(error.message, true);
  }
}

/* ---------------- My courses (school enrolled) ---------------- */

function renderMyCourses() {
  const list = appState.myCourses;
  if (!list.length) {
    const empty = element("div", "empty-state");
    empty.append(element("strong", "", "还没有已选课程"));
    empty.append(element("p", "", "抢到课程后会显示在这里，也可能是学校系统暂未返回数据。"));
    appElements.myCoursesList.replaceChildren(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  list.forEach((course, index) => {
    const row = element("div", "my-course-item");
    row.append(element("span", "my-course-index", String(index + 1)));
    const body = element("div", "my-course-body");
    body.append(element("strong", "", course.course_name || "未命名课程"));
    const meta = element("div", "my-course-meta");
    if (course.teacher_name) meta.append(element("span", "", `教师 ${course.teacher_name}`));
    if (course.teaching_place) meta.append(element("span", "", course.teaching_place));
    if (course.credit) meta.append(element("span", "", `${course.credit} 学分`));
    if (course.course_type_name) meta.append(element("span", "", course.course_type_name));
    if (course.campus_name) meta.append(element("span", "", course.campus_name));
    body.append(meta);
    row.append(body);
    fragment.append(row);
  });
  appElements.myCoursesList.replaceChildren(fragment);
}

function fallbackTimetable(courses) {
  return {
    day_names: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
    period_count: 14,
    entries: [],
    unscheduled: courses.map((course) => ({
      ...course,
      reason: "当前服务尚未返回结构化课表，请重启本地程序后刷新",
    })),
    total_count: courses.length,
    scheduled_count: 0,
    unscheduled_count: courses.length,
  };
}

function timetableEntriesWithLanes(entries) {
  const laidOut = [];
  const appendCluster = (cluster) => {
    if (!cluster.length) return;
    const laneEnds = [];
    const assigned = [];
    for (const entry of cluster) {
      const start = Number(entry.start_period);
      let lane = laneEnds.findIndex((end) => end < start);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = Number(entry.end_period);
      assigned.push({ ...entry, lane });
    }
    const lanes = Math.max(1, laneEnds.length);
    laidOut.push(...assigned.map((entry) => ({ ...entry, lanes })));
  };

  for (let day = 1; day <= 7; day += 1) {
    const dayEntries = entries
      .filter((entry) => Number(entry.day) === day)
      .sort((left, right) => (
        Number(left.start_period) - Number(right.start_period)
        || Number(left.end_period) - Number(right.end_period)
      ));
    let cluster = [];
    let clusterEnd = 0;
    for (const entry of dayEntries) {
      const start = Number(entry.start_period);
      const end = Number(entry.end_period);
      if (cluster.length && start > clusterEnd) {
        appendCluster(cluster);
        cluster = [];
      }
      cluster.push(entry);
      clusterEnd = cluster.length === 1 ? end : Math.max(clusterEnd, end);
    }
    appendCluster(cluster);
  }
  return laidOut;
}

function fitTimetableRows(grid) {
  if (!grid?.isConnected || !appElements.timetableDialog.open) return;
  grid.style.setProperty("--timetable-row-height", `${TIMETABLE_MIN_ROW_HEIGHT}px`);
  grid.getBoundingClientRect();

  let requiredRowHeight = TIMETABLE_MIN_ROW_HEIGHT;
  for (const block of grid.querySelectorAll(".timetable-course")) {
    const periodSpan = Math.max(1, Number(block.dataset.periodSpan || 1));
    requiredRowHeight = Math.max(
      requiredRowHeight,
      Math.ceil((block.scrollHeight + 8) / periodSpan),
    );
  }
  grid.style.setProperty("--timetable-row-height", `${requiredRowHeight}px`);
}

function scheduleTimetableFit(grid) {
  if (appState.timetableFitFrame !== null) {
    window.cancelAnimationFrame(appState.timetableFitFrame);
  }
  appState.timetableFitFrame = window.requestAnimationFrame(() => {
    appState.timetableFitFrame = null;
    fitTimetableRows(grid);
  });
}

function renderTimetable() {
  const timetable = appState.timetable;
  if (!timetable) {
    const empty = element("div", "empty-state");
    empty.append(element("strong", "", "尚未读取课表"));
    empty.append(element("p", "", "点击刷新课表，从学校系统读取当前已选课程。"));
    appElements.timetableContent.replaceChildren(empty);
    appElements.timetableSummary.textContent = "尚未读取学校课表";
    return;
  }

  const entries = Array.isArray(timetable.entries) ? timetable.entries : [];
  const unscheduled = Array.isArray(timetable.unscheduled) ? timetable.unscheduled : [];
  const totalCount = Number(timetable.total_count || appState.myCourses.length || 0);
  const scheduledCount = Number(timetable.scheduled_count || 0);
  appElements.timetableSummary.textContent = `${appState.session?.batch_name || "当前批次"} · ${totalCount} 门课程 · ${scheduledCount} 门已排入网格`;
  appElements.timetableHint.textContent = unscheduled.length
    ? `另有 ${unscheduled.length} 门课程没有可定位的具体星期与节次，已列在课表下方。`
    : "课表来自学校系统当前已选课程，不会执行选课或退课操作。";

  if (!totalCount) {
    const empty = element("div", "empty-state");
    empty.append(element("strong", "", "还没有已选课程"));
    empty.append(element("p", "", "学校系统当前没有返回可放入课表的课程。"));
    appElements.timetableContent.replaceChildren(empty);
    return;
  }

  const content = document.createDocumentFragment();
  const scroll = element("div", "timetable-scroll");
  const grid = element("div", "timetable-grid");
  grid.setAttribute("role", "grid");
  grid.setAttribute("aria-label", "周课表，周一至周日，第 1 至 14 节");

  const corner = element("div", "timetable-corner", "节次");
  corner.style.gridColumn = "1";
  corner.style.gridRow = "1";
  grid.append(corner);

  const dayNames = Array.isArray(timetable.day_names) && timetable.day_names.length === 7
    ? timetable.day_names
    : ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
  dayNames.forEach((dayName, index) => {
    const header = element("div", "timetable-day-header", dayName);
    header.style.gridColumn = String(index + 2);
    header.style.gridRow = "1";
    grid.append(header);
  });

  const periodCount = Math.min(14, Math.max(1, Number(timetable.period_count || 14)));
  for (let period = 1; period <= periodCount; period += 1) {
    const periodCell = element("div", "timetable-period");
    const group = period === 1 ? "上午" : period === 6 ? "下午" : period === 11 ? "晚上" : "";
    if (group) periodCell.append(element("small", "", group));
    periodCell.append(element("strong", "", String(period)));
    periodCell.style.gridColumn = "1";
    periodCell.style.gridRow = String(period + 1);
    grid.append(periodCell);
    for (let day = 1; day <= 7; day += 1) {
      const slot = element(
        "div",
        `timetable-slot${day >= 6 ? " is-weekend" : ""}${period === 6 || period === 11 ? " is-section-start" : ""}`,
      );
      slot.style.gridColumn = String(day + 1);
      slot.style.gridRow = String(period + 1);
      grid.append(slot);
    }
  }

  for (const entry of timetableEntriesWithLanes(entries)) {
    const start = Math.max(1, Math.min(periodCount, Number(entry.start_period || 1)));
    const end = Math.max(start, Math.min(periodCount, Number(entry.end_period || start)));
    const block = element("article", "timetable-course");
    block.style.gridColumn = String(Number(entry.day) + 1);
    block.style.gridRow = `${start + 1} / span ${end - start + 1}`;
    block.style.setProperty("--lane", String(entry.lane || 0));
    block.style.setProperty("--lanes", String(entry.lanes || 1));
    block.dataset.periodSpan = String(end - start + 1);
    block.setAttribute(
      "aria-label",
      `${entry.course_name || "未命名课程"}，${entry.day_name || ""}第 ${start} 至 ${end} 节`,
    );
    block.title = entry.raw_schedule || entry.course_name || "";
    block.append(element("strong", "", entry.course_name || "未命名课程"));
    block.append(
      element(
        "span",
        "",
        [entry.weeks || "周次待定", `${start}-${end} 节`].filter(Boolean).join(" · "),
      ),
    );
    if (entry.location) block.append(element("span", "", entry.location));
    if (entry.teacher_name) block.append(element("span", "", entry.teacher_name));
    grid.append(block);
  }
  scroll.append(grid);
  content.append(scroll);

  const unscheduledSection = element("section", "unscheduled-courses");
  const unscheduledHeader = element("div", "unscheduled-header");
  unscheduledHeader.append(element("h3", "", "未提供可排入课表的具体时间"));
  unscheduledHeader.append(element("span", "", `${unscheduled.length} 门`));
  unscheduledSection.append(unscheduledHeader);
  if (!unscheduled.length) {
    unscheduledSection.append(element("p", "unscheduled-empty", "所有课程均已放入课表网格。"));
  } else {
    const list = element("div", "unscheduled-list");
    for (const course of unscheduled) {
      const row = element("div", "unscheduled-row");
      const body = element("div");
      body.append(element("strong", "", course.course_name || "未命名课程"));
      body.append(
        element(
          "span",
          "",
          [course.teacher_name, course.teaching_place].filter(Boolean).join(" · ") || "学校暂未提供时间地点",
        ),
      );
      row.append(body, element("span", "unscheduled-reason", course.reason || "未提供具体时间"));
      list.append(row);
    }
    unscheduledSection.append(list);
  }
  content.append(unscheduledSection);
  appElements.timetableContent.replaceChildren(content);
  scheduleTimetableFit(grid);
}

function renderTimetableError(message) {
  const state = element("div", "error-state");
  state.append(element("strong", "", "课表读取失败"));
  state.append(element("p", "", message));
  const actions = element("div", "state-actions");
  const retry = element("button", "button button-primary", "重新加载课表");
  retry.type = "button";
  retry.addEventListener("click", () => loadMyCourses());
  actions.append(retry);
  state.append(actions);
  appElements.timetableContent.replaceChildren(state);
}

async function loadMyCourses(silent = false) {
  if (appState.loadingMyCourses) return;
  if (!appState.session?.logged_in) {
    if (!silent) showToast("请先登录后再查看已选课程", true);
    return;
  }
  appState.loadingMyCourses = true;
  const preserveExisting = appState.myCoursesLoaded;
  const previousLabel = appElements.refreshMyCourses.textContent;
  const previousTimetableLabel = appElements.refreshTimetable.textContent;
  appElements.refreshMyCourses.disabled = true;
  appElements.refreshMyCourses.textContent = "刷新中...";
  appElements.refreshTimetable.disabled = true;
  appElements.refreshTimetable.textContent = "刷新中...";
  if (!silent && !preserveExisting) {
    const loading = element("div", "empty-state");
    loading.append(element("strong", "", "正在读取已选课程"));
    loading.append(element("p", "", "正在向学校系统查询，请稍候。"));
    appElements.myCoursesList.replaceChildren(loading);
    if (appElements.timetableDialog.open) {
      const timetableLoading = element("div", "empty-state");
      timetableLoading.append(element("strong", "", "正在读取课表"));
      timetableLoading.append(element("p", "", "正在向学校系统查询当前已选课程。"));
      appElements.timetableContent.replaceChildren(timetableLoading);
      appElements.timetableSummary.textContent = "正在读取学校课表";
    }
  } else if (!silent) {
    appElements.myCoursesHint.textContent = "正在刷新，当前仍显示上次成功结果。";
  }
  try {
    const data = await api("/api/school/enrolled", {
      timeoutMs: SESSION_RECOVERY_TIMEOUT_MS,
    });
    appState.myCourses = Array.isArray(data.courses) ? data.courses : [];
    appState.timetable = data.timetable && typeof data.timetable === "object"
      ? data.timetable
      : fallbackTimetable(appState.myCourses);
    appState.myCoursesLoaded = true;
    appElements.myCoursesHint.textContent = `学校系统当前返回 ${appState.myCourses.length} 门已选课程。`;
    renderMyCourses();
    renderTimetable();
  } catch (error) {
    if (!(error instanceof SessionExpiredError)) {
      if (preserveExisting) {
        appElements.myCoursesHint.textContent = "刷新失败，仍显示上次成功结果。";
        appElements.timetableHint.textContent = "刷新失败，仍显示上次成功读取的课表。";
        if (!silent) showToast(`已选课程刷新失败：${error.message}`, true);
      } else if (!silent) {
        const errorState = element("div", "error-state");
        errorState.append(element("strong", "", "读取失败"));
        errorState.append(element("p", "", error.message));
        const actions = element("div", "state-actions");
        const retry = element("button", "button button-secondary", "重新加载");
        retry.type = "button";
        retry.addEventListener("click", () => loadMyCourses());
        actions.append(retry);
        errorState.append(actions);
        appElements.myCoursesList.replaceChildren(errorState);
        if (appElements.timetableDialog.open) renderTimetableError(error.message);
      }
    }
  } finally {
    appState.loadingMyCourses = false;
    appElements.refreshMyCourses.disabled = false;
    appElements.refreshMyCourses.textContent = previousLabel;
    appElements.refreshTimetable.disabled = false;
    appElements.refreshTimetable.textContent = previousTimetableLabel;
  }
}

/* ---------------- Enrollment progress polling ---------------- */

function renderProgress(data) {
  const courses = (data && data.courses) || [];
  const hasProgress = courses.length > 0;
  appElements.enrollProgress.hidden = !hasProgress;
  if (!hasProgress) {
    appElements.taskControlButton.hidden = true;
    return;
  }

  const counts = data.counts || { total: 0, success: 0, failed: 0, active: 0 };
  const paused = Boolean(data.paused);
  const pauseAcknowledged = Boolean(data.pause_acknowledged);
  const stopping = Boolean(data.stopping);
  const recovering = Boolean(appState.session?.relogin_in_progress);
  appElements.progressCounts.textContent = `${counts.success} 抢到 · ${counts.failed} 失败 · ${counts.active} 待处理`;
  const completed = counts.success + counts.failed;
  const pct = counts.total ? Math.round((completed / counts.total) * 100) : 0;
  appElements.progressBarFill.style.width = `${pct}%`;

  appElements.progressState.className = "status-pill status-neutral";
  if (!data.running) {
    appElements.progressState.textContent = "任务已结束";
  } else if (stopping) {
    appElements.progressState.textContent = "正在结束";
    appElements.progressState.className = "status-pill status-warning";
  } else if (recovering) {
    appElements.progressState.textContent = "正在重新登录";
    appElements.progressState.className = "status-pill status-warning";
  } else if (paused) {
    appElements.progressState.textContent = pauseAcknowledged ? "已暂停" : "正在暂停";
    appElements.progressState.className = "status-pill status-warning";
  } else {
    appElements.progressState.textContent = "抢课中";
    appElements.progressState.className = "status-pill status-success";
  }

  appElements.taskControlButton.hidden = !data.running || stopping;
  appElements.taskControlButton.textContent = paused
    ? pauseAcknowledged ? "继续任务" : "正在暂停"
    : "暂停任务";
  appElements.taskControlButton.className = paused
    ? "button button-primary"
    : "button button-secondary";
  appElements.taskControlButton.disabled = appState.taskControlPending
    || stopping
    || (paused && (!pauseAcknowledged || recovering));
  if (stopping) {
    appElements.progressNotice.textContent = data.stopping_reason
      || "待处理课程已清空，后台任务正在结束。";
  } else if (recovering) {
    appElements.progressNotice.textContent = "学校会话已过期，正在自动重新登录；课程和当前进度均已保留。";
  } else if (paused && !pauseAcknowledged) {
    appElements.progressNotice.textContent = "正在等待当前学校请求结束；安全暂停后即可移除课程。";
  } else if (paused) {
    appElements.progressNotice.textContent = data.pause_reason
      || "任务已暂停；点击继续后从现有清单和尝试次数接着运行。";
  } else if (data.running) {
    appElements.progressNotice.textContent = "任务运行中；点击暂停后，会在当前学校请求结束后停止发送新请求。";
  } else {
    appElements.progressNotice.textContent = counts.active
      ? "仍有待处理课程，可重新启动任务。"
      : "本轮任务已经结束。";
  }

  const fragment = document.createDocumentFragment();
  for (const course of courses) {
    const row = element("div", "progress-row");
    const info = element("div");
    info.append(element("span", "p-name", course.name || course.id));
    info.append(
      element(
        "span",
        "p-msg",
        [course.campus_name, course.message].filter(Boolean).join(" · "),
      ),
    );
    const side = element("div", "cart-item-actions");
    const statusClass = course.status === "SUCCESS" ? "status-success" : course.status === "FAILED" ? "status-danger" : "status-warning";
    const statusLabel = stopping && course.status === "ENROLLING"
      ? "正在结束"
      : paused && course.status === "ENROLLING"
        ? pauseAcknowledged ? "已暂停" : "正在暂停"
      : statusNames[course.status] || course.status;
    side.append(element("span", `status-pill ${statusClass}`, statusLabel));
    side.append(element("span", "p-attempts", `${course.attempts || 0} 次`));
    row.append(info, side);
    fragment.append(row);
  }
  appElements.progressRows.replaceChildren(fragment);
}

async function toggleEnrollmentPause() {
  if (appState.taskControlPending || !appState.progress?.running) return;
  const paused = Boolean(appState.progress.paused);
  appState.taskControlPending = true;
  renderProgress(appState.progress);
  try {
    const result = await api(paused ? "/api/enroll/resume" : "/api/enroll/pause", {
      method: "POST",
      timeoutMs: SESSION_RECOVERY_TIMEOUT_MS,
    });
    if (result.progress) {
      const cartControlsChanged = applyProgressTaskState(result.progress);
      if (cartControlsChanged) renderCart();
    }
    showToast(result.message || (paused ? "抢课任务已继续" : "抢课任务已暂停"), false, true);
    renderProgress(appState.progress);
    updateTaskIndicator();
    setPhasePresentation();
    syncEnrollControls();
  } catch (error) {
    if (error.requiresManualLogin) showSessionDialog(error.message);
    else if (!(error instanceof SessionExpiredError)) showToast(error.message, true);
    if (["PHASE_NOT_ALLOWED", "BATCH_UNAVAILABLE"].includes(error.code)) {
      await loadSession(false);
    }
  } finally {
    appState.taskControlPending = false;
    if (appState.progress) renderProgress(appState.progress);
  }
}

async function loadEnrollProgress() {
  if (appState.loadingProgress) return;
  appState.loadingProgress = true;
  let data;
  try {
    data = await api("/api/enroll/status");
  } catch (error) {
    if (error instanceof SessionExpiredError) stopProgressPolling();
    return;
  } finally {
    appState.loadingProgress = false;
  }
  const cartControlsChanged = applyProgressTaskState(data);
  renderProgress(data);
  updateTaskIndicator();
  setPhasePresentation();
  syncEnrollControls();
  if (cartControlsChanged) renderCart();

  for (const course of data.courses || []) {
    if (course.status === "SUCCESS" && !appState.knownSuccessIds.has(course.id)) {
      appState.knownSuccessIds.add(course.id);
      showToast(`${course.name} 已加入我的课程`, false, true);
    }
  }

  if (data.running) {
    appState.wasTaskRunning = true;
  } else if (appState.wasTaskRunning) {
    appState.wasTaskRunning = false;
    stopProgressPolling();
    const counts = data.counts || { success: 0, failed: 0 };
    showToast(
      `抢课任务结束：成功 ${counts.success} 门，失败 ${counts.failed} 门，保留 ${counts.active || 0} 门`,
      counts.failed > 0 && counts.success === 0,
    );
    await loadCart();
    await loadMyCourses(true);
    await loadSession(false, false);
    invalidateCatalogCaches();
    if (!courseCatalogBlocked()) await refreshCurrentView();
  }
}

function startProgressPolling() {
  if (appState.progressTimer) return;
  loadEnrollProgress();
  appState.progressTimer = window.setInterval(loadEnrollProgress, 1500);
}

function stopProgressPolling() {
  if (appState.progressTimer) {
    window.clearInterval(appState.progressTimer);
    appState.progressTimer = null;
  }
}

function startSessionPolling() {
  if (appState.sessionTimer) return;
  appState.sessionTimer = window.setInterval(() => {
    if (!appState.refreshingPhase) loadSession(false);
  }, SESSION_POLL_INTERVAL_MS);
}

function stopSessionPolling() {
  if (appState.sessionTimer) {
    window.clearInterval(appState.sessionTimer);
    appState.sessionTimer = null;
  }
}

async function startEnrollment() {
  if (!appElements.phaseConfirmation.checked || !appState.grabPhase) return;
  appElements.startEnroll.disabled = true;
  appElements.enrollMessage.textContent = "正在启动后台任务...";
  try {
    const result = await api("/api/enroll/courses", {
      method: "POST",
      body: JSON.stringify({ confirmed_phase: true }),
    });
    if (result.is_error) throw new Error(result.message);
    appState.knownSuccessIds = new Set();
    appElements.enrollDialog.close();
    showToast(result.message || "后台任务已启动");
    await loadSession(false);
    await loadCart();
    startProgressPolling();
  } catch (error) {
    appElements.enrollMessage.textContent = error.message;
  } finally {
    appElements.startEnroll.disabled = !appElements.phaseConfirmation.checked;
  }
}

appElements.categoryList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-type]");
  if (!button || button.dataset.type === appState.type) return;
  for (const item of appElements.categoryList.querySelectorAll("[data-type]")) {
    item.classList.toggle("is-active", item === button);
  }
  appState.type = button.dataset.type;
  appState.page = 1;
  appState.searchKeyword = "";
  appState.searchResults = [];
  appState.searchPage = 1;
  appElements.courseSearch.value = "";
  abortCatalogFetch();
  loadCourses();
});

let searchDebounceTimer = null;
appElements.courseSearch.addEventListener("input", () => {
  if (searchDebounceTimer) window.clearTimeout(searchDebounceTimer);
  searchDebounceTimer = window.setTimeout(() => {
    searchDebounceTimer = null;
    handleSearchInput();
  }, SEARCH_DEBOUNCE_MS);
});
appElements.campusSelect.addEventListener("change", () => {
  switchCampus(appElements.campusSelect.value);
});
appElements.refreshCourses.addEventListener("click", refreshCurrentView);
appElements.refreshPhase.addEventListener("click", refreshPhaseAndCourses);
appElements.previousPage.addEventListener("click", () => {
  if (isFilterActive()) {
    if (appState.searchPage > 1) {
      appState.searchPage -= 1;
      applyCourseFilter();
      renderCourses();
      updatePagination();
    }
    return;
  }
  if (appState.page > 1) {
    appState.page -= 1;
    loadCourses();
  }
});
appElements.nextPage.addEventListener("click", () => {
  if (isFilterActive()) {
    const results = Array.isArray(appState.searchResults) ? appState.searchResults : [];
    const totalPages = Math.max(1, Math.ceil(results.length / FILTER_PAGE_SIZE));
    if (appState.searchPage < totalPages) {
      appState.searchPage += 1;
      applyCourseFilter();
      renderCourses();
      updatePagination();
    }
    return;
  }
  appState.page += 1;
  loadCourses();
});
appElements.openCart.addEventListener("click", async () => {
  await loadCart();
  if (appState.session?.task_running) await loadEnrollProgress();
  appElements.cartDialog.showModal();
});
appElements.openMyCourses.addEventListener("click", async () => {
  appElements.myCoursesDialog.showModal();
  await loadMyCourses();
});
appElements.openTimetable.addEventListener("click", async () => {
  appElements.timetableDialog.showModal();
  renderTimetable();
  if (!appState.myCoursesLoaded) await loadMyCourses();
});
appElements.refreshMyCourses.addEventListener("click", () => loadMyCourses());
appElements.refreshTimetable.addEventListener("click", () => loadMyCourses());
if (typeof window.addEventListener === "function") {
  window.addEventListener("resize", () => {
    if (appState.timetableResizeTimer !== null) {
      window.clearTimeout(appState.timetableResizeTimer);
    }
    appState.timetableResizeTimer = window.setTimeout(() => {
      appState.timetableResizeTimer = null;
      const grid = appElements.timetableContent.querySelector(".timetable-grid");
      if (grid) scheduleTimetableFit(grid);
    }, 120);
  });
}
appElements.openEnrollConfirm.addEventListener("click", () => {
  if (!appState.grabPhase) return;
  appElements.phaseConfirmation.checked = false;
  appElements.startEnroll.disabled = true;
  appElements.enrollMessage.textContent = "";
  appElements.enrollDialog.showModal();
});
appElements.phaseConfirmation.addEventListener("change", () => {
  appElements.startEnroll.disabled = !appElements.phaseConfirmation.checked;
});
appElements.startEnroll.addEventListener("click", startEnrollment);
appElements.taskControlButton.addEventListener("click", toggleEnrollmentPause);
appElements.openSchoolRaw?.addEventListener("click", () => {
  openSchoolRawPage();
});
appElements.logout.addEventListener("click", async () => {
  try {
    const result = await api("/api/logout", { method: "POST" });
    if (result.is_error) throw new Error(result.message);
    stopProgressPolling();
    stopSessionPolling();
    window.location.assign(versionedPage("/login"));
  } catch (error) {
    if (!(error instanceof SessionExpiredError)) showToast(error.message, true);
  }
});

function openSchoolRawPage() {
  if (!appState.session?.logged_in) {
    showToast("请先登录，再打开学校原始页面", true);
    return;
  }
  // Same-origin path via the local reverse proxy; reuses the shared school
  // session, so no second login (which would kick the API session out).
  const target = `${window.location.origin}/proxy/bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do`;
  window.open(target, "_blank", "noopener,noreferrer");
}

for (const closeButton of document.querySelectorAll("[data-close-dialog]")) {
  closeButton.addEventListener("click", () => {
    document.querySelector(`#${closeButton.dataset.closeDialog}`)?.close();
  });
}

async function initializeApp() {
  await loadSession(true);
  startSessionPolling();
  appElements.brandLink.href = versionedPage("/");
  appElements.sessionLoginLink.href = versionedPage("/login");
  await loadCart();
  if (appState.session?.logged_in) await loadCourses();
  else renderState("尚未登录", "返回登录页完成学号、密码、卡密和验证码校验。");
}

initializeApp();
