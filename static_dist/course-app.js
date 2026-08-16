"use strict";

const REQUEST_TIMEOUT_MS = 30000;
const SESSION_RECOVERY_TIMEOUT_MS = 180000;

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
  cart: [],
  session: null,
  loadingCourses: false,
  courseRequestController: null,
  courseRequestId: 0,
  courseDataKey: "",
  catalogBlockedCode: "",
  loadingSession: false,
  refreshingPhase: false,
  preselection: false,
  closedPhase: false,
  grabPhase: false,
  myCourses: [],
  myCoursesLoaded: false,
  loadingMyCourses: false,
  myCoursesView: "grid",
  showCartOnSchedule: true,
  progress: null,
  progressTimer: null,
  loadingProgress: false,
  knownSuccessIds: new Set(),
  wasTaskRunning: false,
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
  courseTypeCode: document.querySelector("#courseTypeCode"),
  courseTitle: document.querySelector("#courseTitle"),
  courseSummary: document.querySelector("#courseSummary"),
  courseSearch: document.querySelector("#courseSearch"),
  refreshCourses: document.querySelector("#refreshCourses"),
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
  myCoursesScheduleWrap: document.querySelector("#myCoursesScheduleWrap"),
  scheduleViewGrid: document.querySelector("#scheduleViewGrid"),
  scheduleViewList: document.querySelector("#scheduleViewList"),
  showPendingSwitch: document.querySelector("#showPendingSwitch"),
  enrollProgress: document.querySelector("#enrollProgress"),
  progressCounts: document.querySelector("#progressCounts"),
  progressBarFill: document.querySelector("#progressBarFill"),
  progressRows: document.querySelector("#progressRows"),
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

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
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
  appState.session = session;
  appElements.studentLabel.textContent = session.logged_in
    ? `学号 ${session.student_id}`
    : "未登录";
  updateTaskIndicator();
  setPhasePresentation();
  if (session.task_running) startProgressPolling();
}

function updateTaskIndicator() {
  const running = Boolean(appState.session?.task_running);
  appElements.taskIndicator.hidden = !running;
  if (!running) return;
  const counts = appState.progress?.counts;
  appElements.taskIndicator.textContent = counts
    ? `抢课中 ${counts.success}/${counts.total}`
    : "抢课中";
}

async function loadSession(showDialog = true) {
  if (appState.loadingSession) return appState.session;
  appState.loadingSession = true;
  const previousTaskState = Boolean(appState.session?.task_running);
  try {
    const session = await api("/api/session");
    applySessionData(session);
    if (!appState.session.logged_in && showDialog) {
      showSessionDialog("当前没有有效登录状态，请返回登录页完成登录。");
    }
    if (previousTaskState !== Boolean(appState.session.task_running)) {
      await loadCart();
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
          is_choose: String(classInfo.is_choose || ""),
          is_conflict: String(classInfo.is_conflict || ""),
          is_full: String(classInfo.is_full || ""),
          teaching_place: String(classInfo.teaching_place || ""),
          course_name: String(course.course_name || ""),
          teacher_name: String(classInfo.teacher_name || ""),
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

function renderCourses() {
  const keyword = appElements.courseSearch.value.trim().toLowerCase();
  const filtered = appState.courses.filter((course) => {
    if (!keyword) return true;
    const teachingText = (course.tcList || [])
      .map((item) => `${item.teacher_name || ""} ${item.teaching_place || ""}`)
      .join(" ");
    return `${course.course_name || ""} ${course.course_number || ""} ${course.department_name || ""} ${teachingText}`
      .toLowerCase()
      .includes(keyword);
  });

  if (!filtered.length) {
    renderState(
      keyword ? "本页没有匹配结果" : "本页没有课程",
      keyword ? "换一个关键词，或切换课程目录。" : "学校系统当前没有返回该目录的课程。",
    );
    return;
  }

  const fragment = document.createDocumentFragment();
  filtered.forEach((course, index) => {
    const details = element("details", "course-group");
    if (index === 0) details.open = true;
    details.style.animationDelay = `${Math.min(index, 8) * 40}ms`;
    const summary = element("summary");
    const main = element("div", "course-summary-main");
    main.append(element("strong", "", course.course_name || "未命名课程"));
    main.append(
      element(
        "span",
        "",
        [course.course_number, course.department_name, course.sport_name].filter(Boolean).join(" · ") || "课程信息待定",
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

function updatePagination() {
  const totalPages = Math.max(1, Math.ceil(appState.totalCount / 10));
  appElements.pageLabel.textContent = `第 ${appState.page} / ${totalPages} 页`;
  const blocked = courseCatalogBlocked();
  appElements.previousPage.disabled = blocked || appState.page <= 1 || appState.loadingCourses;
  appElements.nextPage.disabled = blocked || appState.page >= totalPages || appState.loadingCourses;
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
      appElements.refreshCourses.disabled = appState.refreshingPhase;
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
    else await loadCourses({ preserveExisting: true });
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
  else await loadCourses({ preserveExisting: true });
}

function syncEnrollControls() {
  const running = Boolean(appState.session?.task_running);
  const hasPending = appState.cart.some((item) => (item.status || "PENDING") === "PENDING");
  appElements.openEnrollConfirm.disabled = !appState.grabPhase || running || !hasPending;
  if (running) {
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
    copy.append(element("span", "", `${item.type} · ${statusNames[item.status] || item.status || "待启动"}`));
    const actions = element("div", "cart-item-actions");
    const statusClass = item.status === "SUCCESS" ? "status-success" : item.status === "FAILED" ? "status-danger" : item.status === "ENROLLING" ? "status-warning" : "status-neutral";
    actions.append(element("span", `status-pill ${statusClass}`, statusNames[item.status] || "待启动"));
    const remove = element("button", "button button-quiet", "移除");
    remove.type = "button";
    remove.disabled = Boolean(appState.session?.task_running) || item.status === "ENROLLING";
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        const result = await api(`/api/courses/delete?id=${encodeURIComponent(item.id)}`, { method: "POST" });
        if (result.is_error) throw new Error(result.message);
        showToast(result.message || "已移除");
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
    body.append(meta);
    row.append(body);
    fragment.append(row);
  });
  appElements.myCoursesList.replaceChildren(fragment);
}

/* ---------------- My courses schedule (weekly grid) ---------------- */

const SCHEDULE_COLORS = 12;
const BREAK_PERIODS = [3, 6, 9, 11];

/**
 * Build a course-color mapping so visually distinct courses stand out.
 * Keyed by course_name|teacher_name for enrolled courses.
 */
function buildScheduleColorMap(courses) {
  const colorMap = new Map();
  let nextColor = 0;
  for (const course of courses) {
    const key = (course.course_name || "未命名课程") + "|" + (course.teacher_name || "");
    if (colorMap.has(key)) continue;
    colorMap.set(key, nextColor % SCHEDULE_COLORS);
    nextColor += 1;
  }
  return colorMap;
}

/**
 * Parse a course-like object's teaching_place into schedule slots
 * and split them into placed (on the standard 14-period grid) and
 * unplaced (go to the right-side non-standard list).
 */
function collectScheduleEntries(courseLike, color, pending, placedSlots, unplaced) {
  const slots = parseScheduleSlots(courseLike.teaching_place || "");
  let hasPlaced = false;
  for (const slot of slots) {
    const ok =
      slot.dayOfWeek >= 0 && slot.dayOfWeek <= 6 &&
      slot.startPeriod >= 1 && slot.endPeriod <= MAX_PERIOD &&
      slot.startPeriod <= slot.endPeriod;
    if (ok) {
      placedSlots.push({ course: courseLike, slot, color, pending });
      hasPlaced = true;
    }
  }
  if (!hasPlaced) {
    unplaced.push({
      course: courseLike,
      color,
      pending,
      reason: slots.length ? "时间超出标准节次" : "无时间信息",
    });
  }
}

function formatCourseTooltip(course, slot, pending) {
  const parts = [];
  parts.push(course.course_name || "未命名课程");
  if (pending) parts.push("（待选）");
  if (course.teacher_name) parts.push("教师：" + course.teacher_name);
  if (slot.weeks) parts.push("周数：" + slot.weeks);
  if (slot.dayLabel) parts.push("星期：" + slot.dayLabel);
  parts.push("节次：第" + slot.startPeriod + "-" + slot.endPeriod + "节");
  if (slot.place) parts.push("地点：" + slot.place);
  if (course.teaching_place && course.teaching_place !== slot.raw) {
    parts.push("完整时间地点：" + course.teaching_place);
  }
  return parts.join("\n");
}

function buildCourseBlock(placed) {
  const block = element("div", "schedule-course" + (placed.pending ? " is-pending" : ""));
  block.setAttribute("data-color", String(placed.color));
  block.title = formatCourseTooltip(placed.course, placed.slot, placed.pending);
  const nameEl = element("strong", "", placed.course.course_name || "未命名课程");
  block.append(nameEl);
  if (placed.pending) {
    block.append(element("span", "schedule-pending-badge", "待选"));
  }
  if (placed.slot.weeks) {
    block.append(element("span", "schedule-weeks", placed.slot.weeks));
  }
  if (placed.slot.place) {
    block.append(element("span", "schedule-place", placed.slot.place));
  }
  if (placed.course.teacher_name) {
    block.append(element("span", "schedule-teacher", placed.course.teacher_name));
  }
  return block;
}

function renderMyCoursesSchedule() {
  const wrap = appElements.myCoursesScheduleWrap;
  const courses = appState.myCourses;

  /* Determine pending (cart) items to show */
  const enrolledIds = new Set(courses.map((c) => String(c.teaching_class_id || "")));
  const pendingItems = appState.showCartOnSchedule
    ? appState.cart.filter(
        (item) => (item.status || "PENDING") !== "SUCCESS" && !enrolledIds.has(String(item.id)),
      )
    : [];

  if (!courses.length && !pendingItems.length) {
    const empty = element("div", "empty-state");
    empty.append(element("strong", "", "还没有已选课程"));
    empty.append(element("p", "", "抢到课程后会显示在这里，也可能是学校系统暂未返回数据。"));
    wrap.replaceChildren(empty);
    return;
  }

  /* Build color map (enrolled + pending share the same palette space) */
  const colorMap = buildScheduleColorMap(courses);
  let pendingColorBase = colorMap.size;

  const placedSlots = [];
  const unplaced = [];

  for (const course of courses) {
    const key = (course.course_name || "未命名课程") + "|" + (course.teacher_name || "");
    collectScheduleEntries(course, colorMap.get(key) || 0, false, placedSlots, unplaced);
  }

  for (const item of pendingItems) {
    const displayName = item.course_name || item.name || "未命名课程";
    const teacherName = item.teacher_name || "";
    const key = displayName + "|" + teacherName;
    if (!colorMap.has(key)) {
      colorMap.set(key, pendingColorBase % SCHEDULE_COLORS);
      pendingColorBase += 1;
    }
    collectScheduleEntries(
      { course_name: displayName, teacher_name: teacherName, teaching_place: item.teaching_place },
      colorMap.get(key),
      true,
      placedSlots,
      unplaced,
    );
  }

  /* ---- Build layout ---- */
  const container = element("div", "schedule-layout");

  /* Left: weekly grid (14 per-period rows) */
  const gridWrap = element("div", "schedule-grid-wrap");

  const today = new Date().getDay();
  const todayIndex = today === 0 ? 6 : today - 1;

  const grid = element("div", "schedule-grid");
  grid.style.gridTemplateColumns = "58px repeat(7, minmax(78px, 1fr))";
  grid.style.gridTemplateRows = "auto repeat(" + MAX_PERIOD + ", minmax(28px, auto))";

  /* Corner */
  const corner = element("div", "schedule-corner", "节");
  corner.style.gridRow = "1";
  corner.style.gridColumn = "1";
  grid.append(corner);

  /* Day headers */
  for (let d = 0; d < 7; d++) {
    const header = element("div", "schedule-col-header");
    if (d === todayIndex) header.classList.add("is-today");
    header.append(element("span", "", DAYS_OF_WEEK[d].label));
    header.style.gridRow = "1";
    header.style.gridColumn = String(d + 2);
    grid.append(header);
  }

  /* Row labels + background cells (one per period) */
  for (let p = 1; p <= MAX_PERIOD; p++) {
    const isBreak = BREAK_PERIODS.includes(p);
    const row = p + 1;

    const label = element("div", "schedule-row-label" + (isBreak ? " is-break" : ""));
    label.append(element("strong", "", String(p)));
    label.append(element("small", "", PERIODS[p - 1].timeLabel));
    label.style.gridRow = String(row);
    label.style.gridColumn = "1";
    grid.append(label);

    for (let d = 0; d < 7; d++) {
      const cell = element("div", "schedule-cell" + (isBreak ? " is-break" : ""));
      if (d === todayIndex) cell.classList.add("is-today");
      cell.style.gridRow = String(row);
      cell.style.gridColumn = String(d + 2);
      grid.append(cell);
    }
  }

  /* Course blocks: group by day|startPeriod|endPeriod, each group spans rows */
  const stackMap = new Map();
  for (const placed of placedSlots) {
    const key = placed.slot.dayOfWeek + "|" + placed.slot.startPeriod + "|" + placed.slot.endPeriod;
    if (!stackMap.has(key)) stackMap.set(key, []);
    stackMap.get(key).push(placed);
  }

  for (const entries of stackMap.values()) {
    const first = entries[0].slot;
    const stack = element("div", "schedule-stack");
    stack.style.gridColumn = String(first.dayOfWeek + 2);
    stack.style.gridRow = (first.startPeriod + 1) + " / " + (first.endPeriod + 2);
    for (const placed of entries) {
      stack.append(buildCourseBlock(placed));
    }
    grid.append(stack);
  }

  gridWrap.append(grid);

  /* Right: non-standard time courses */
  const nonStandard = element("div", "schedule-nonstandard");
  nonStandard.append(element("p", "schedule-nonstandard-title", "非标准时间课程"));

  if (unplaced.length) {
    for (const item of unplaced) {
      const nsItem = element("div", "schedule-nonstandard-item" + (item.pending ? " is-pending" : ""));
      nsItem.setAttribute("data-color", String(item.color));
      const nsTitleParts = [item.course.course_name || "未命名课程"];
      if (item.pending) nsTitleParts.push("（待选）");
      if (item.course.teacher_name) nsTitleParts.push("教师：" + item.course.teacher_name);
      if (item.course.teaching_place) nsTitleParts.push("时间地点：" + item.course.teaching_place);
      nsItem.title = nsTitleParts.join("\n");
      nsItem.append(element("strong", "", item.course.course_name || "未命名课程"));
      if (item.pending) {
        nsItem.append(element("span", "schedule-pending-badge", "待选"));
      }
      const meta = element("div", "schedule-nonstandard-meta");
      if (item.course.teacher_name) meta.append(element("span", "", item.course.teacher_name));
      if (item.course.teaching_place) meta.append(element("span", "", item.course.teaching_place));
      meta.append(element("span", "", item.reason));
      nsItem.append(meta);
      nonStandard.append(nsItem);
    }
  } else {
    nonStandard.append(element("p", "", "所有课e程均在标准时段内。"));
  }

  /* Legend */
  if (pendingItems.length) {
    const legend = element("p", "schedule-legend");
    legend.append(element("i", "legend-dot legend-pending"));
    legend.append(document.createTextNode("虚化块为选课清单中的待选课程（未实际选上）"));
    nonStandard.append(legend);
  }

  container.append(gridWrap, nonStandard);
  wrap.replaceChildren(container);
}

function switchMyCoursesView(view) {
  appState.myCoursesView = view;
  const isGrid = view === "grid";
  appElements.scheduleViewGrid.classList.toggle("is-active", isGrid);
  appElements.scheduleViewList.classList.toggle("is-active", !isGrid);
  appElements.myCoursesScheduleWrap.hidden = !isGrid;
  appElements.myCoursesList.hidden = isGrid;
  appElements.myCoursesDialog.classList.toggle("is-wide-schedule", isGrid);
  if (isGrid) renderMyCoursesSchedule();
  else renderMyCourses();
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
  appElements.refreshMyCourses.disabled = true;
  appElements.refreshMyCourses.textContent = "刷新中...";
  if (!silent && !preserveExisting) {
    const loading = element("div", "empty-state");
    loading.append(element("strong", "", "正在读取已选课程"));
    loading.append(element("p", "", "正在向学校系统查询，请稍候。"));
    appElements.myCoursesList.replaceChildren(loading);
  } else if (!silent) {
    appElements.myCoursesHint.textContent = "正在刷新，当前仍显示上次成功结果。";
  }
  try {
    const data = await api("/api/school/enrolled", {
      timeoutMs: SESSION_RECOVERY_TIMEOUT_MS,
    });
    appState.myCourses = Array.isArray(data.courses) ? data.courses : [];
    appState.myCoursesLoaded = true;
    appElements.myCoursesHint.textContent = `学校系统当前返回 ${appState.myCourses.length} 门已选课程。`;
    renderMyCourses();
    renderMyCoursesSchedule();
  } catch (error) {
    if (!(error instanceof SessionExpiredError)) {
      if (preserveExisting) {
        appElements.myCoursesHint.textContent = "刷新失败，仍显示上次成功结果。";
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
      }
    }
  } finally {
    appState.loadingMyCourses = false;
    appElements.refreshMyCourses.disabled = false;
    appElements.refreshMyCourses.textContent = previousLabel;
  }
}

/* ---------------- Enrollment progress polling ---------------- */

function renderProgress(data) {
  const courses = (data && data.courses) || [];
  const hasProgress = courses.length > 0;
  appElements.enrollProgress.hidden = !hasProgress;
  if (!hasProgress) return;

  const counts = data.counts || { total: 0, success: 0, failed: 0, active: 0 };
  appElements.progressCounts.textContent = `${counts.success} 抢到 · ${counts.failed} 失败 · ${counts.active} 进行中`;
  const completed = counts.success + counts.failed;
  const pct = counts.total ? Math.round((completed / counts.total) * 100) : 0;
  appElements.progressBarFill.style.width = `${pct}%`;

  const fragment = document.createDocumentFragment();
  for (const course of courses) {
    const row = element("div", "progress-row");
    const info = element("div");
    info.append(element("span", "p-name", course.name || course.id));
    info.append(element("span", "p-msg", course.message || ""));
    const side = element("div", "cart-item-actions");
    const statusClass = course.status === "SUCCESS" ? "status-success" : course.status === "FAILED" ? "status-danger" : "status-warning";
    side.append(element("span", `status-pill ${statusClass}`, statusNames[course.status] || course.status));
    side.append(element("span", "p-attempts", `${course.attempts || 0} 次`));
    row.append(info, side);
    fragment.append(row);
  }
  appElements.progressRows.replaceChildren(fragment);
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
  appState.progress = data;
  renderProgress(data);
  updateTaskIndicator();

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
    showToast(`抢课任务结束：成功 ${counts.success} 门，失败 ${counts.failed} 门`, counts.failed > 0 && counts.success === 0);
    await loadCart();
    await loadMyCourses(true);
    await loadSession(false);
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
  appElements.courseSearch.value = "";
  loadCourses();
});

appElements.courseSearch.addEventListener("input", renderCourses);
appElements.refreshCourses.addEventListener("click", refreshCurrentView);
appElements.refreshPhase.addEventListener("click", refreshPhaseAndCourses);
appElements.previousPage.addEventListener("click", () => {
  if (appState.page > 1) {
    appState.page -= 1;
    loadCourses();
  }
});
appElements.nextPage.addEventListener("click", () => {
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
  switchMyCoursesView(appState.myCoursesView);
  await loadCart();
  await loadMyCourses();
});
appElements.scheduleViewGrid.addEventListener("click", () => switchMyCoursesView("grid"));
appElements.scheduleViewList.addEventListener("click", () => switchMyCoursesView("list"));
appElements.showPendingSwitch.addEventListener("change", () => {
  appState.showCartOnSchedule = appElements.showPendingSwitch.checked;
  renderMyCoursesSchedule();
});
appElements.scheduleViewGrid.addEventListener("click", () => switchMyCoursesView("grid"));
appElements.scheduleViewList.addEventListener("click", () => switchMyCoursesView("list"));
appElements.refreshMyCourses.addEventListener("click", () => loadMyCourses());
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
appElements.openSchoolRaw.addEventListener("click", () => {
  openSchoolRawPage();
});
appElements.logout.addEventListener("click", async () => {
  try {
    const result = await api("/api/logout", { method: "POST" });
    if (result.is_error) throw new Error(result.message);
    stopProgressPolling();
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
  appElements.brandLink.href = versionedPage("/");
  appElements.sessionLoginLink.href = versionedPage("/login");
  await loadCart();
  if (appState.session?.logged_in) await loadCourses();
  else renderState("尚未登录", "返回登录页完成学号、密码、卡密和验证码校验。");
  window.setInterval(() => {
    if (!appState.refreshingPhase) loadSession(false);
  }, 5000);
}

initializeApp();
