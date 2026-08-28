"""Functional regression tests for course search + pagination.

The Web UI must filter across the entire catalog of the current category and
re-paginate the filtered results, instead of filtering inside the already
fetched server page. These tests drive the real ``course-app.js`` in Node with
a minimal DOM stub and a scripted ``fetch``, then assert on the observable
behavior (school requests made, filtered counts, page labels, rendered rows).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "static_dist" / "course-app.js"

# Browser environment stubs: permissive DOM nodes plus a scripted fetch that
# serves a fake catalog of 35 courses (4 school pages of 10) where courses at
# index i % 3 == 0 are math courses (12 in total, matching keyword "数学").
HARNESS_JS = r"""
"use strict";

const fs = require("node:fs");

const nodesById = new Map();
function makeNode(tag = "div") {
  const node = {
    tagName: String(tag).toUpperCase(),
    children: [],
    style: {},
    dataset: {},
    className: "",
    textContent: "",
    hidden: false,
    disabled: false,
    open: false,
    href: "",
    type: "",
    handlers: {},
    attributes: {},
    append(...items) {
      for (const item of items) node.children.push(item);
    },
    appendChild(item) {
      node.children.push(item);
      return item;
    },
    replaceChildren(...items) {
      const flattened = [];
      for (const item of items) {
        // 真实 DOM 插入 DocumentFragment 时会解包为其子节点
        if (item && item.tagName === "#FRAGMENT") flattened.push(...item.children);
        else flattened.push(item);
      }
      node.children = flattened;
    },
    remove() {},
    setAttribute(key, value) {
      node.attributes[key] = value;
    },
    addEventListener(type, handler) {
      (node.handlers[type] = node.handlers[type] || []).push(handler);
    },
    click() {
      for (const handler of node.handlers.click || []) handler({ target: node, preventDefault() {} });
    },
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
    showModal() {},
    close() {},
    focus() {},
  };
  return node;
}

globalThis.document = {
  querySelector(selector) {
    if (typeof selector === "string" && selector.startsWith("#")) {
      const id = selector.slice(1);
      if (!nodesById.has(id)) nodesById.set(id, makeNode());
      return nodesById.get(id);
    }
    return makeNode();
  },
  querySelectorAll() {
    return [];
  },
  createElement: (tag) => makeNode(tag),
  createDocumentFragment: () => makeNode("#fragment"),
};

globalThis.window = {
  location: { search: "", origin: "http://course.test", assign() {} },
  setTimeout: (handler, delay, ...rest) => setTimeout(handler, delay, ...rest),
  clearTimeout: (id) => clearTimeout(id),
  setInterval: (handler, delay, ...rest) => setInterval(handler, delay, ...rest),
  clearInterval: (id) => clearInterval(id),
};

const PAGE_SIZE = 10;
const schoolThrottle = {
  rejectOncePages: new Set(),
  rejectedOncePages: new Set(),
  rejectAlwaysPages: new Set(),
};
const schoolTimeline = [];
function schoolRejectedResponse() {
  return {
    ok: false,
    status: 502,
    text: async () => JSON.stringify({
      message: "学校暂时拒绝了课程目录请求，请稍后刷新；本地清单不受影响。",
      is_error: true,
      error_code: "SCHOOL_COURSE_REJECTED",
      retryable: true,
      requires_manual_login: false,
    }),
  };
}
const fakeCourses = [];
for (let i = 0; i < 35; i += 1) {
  const math = i % 3 === 0;
  fakeCourses.push({
    course_name: math ? `高等数学${i}` : `大学英语${i}`,
    course_number: `C${String(i).padStart(3, "0")}`,
    department_name: "通识学院",
    sport_name: "",
    number: "",
    selected: false,
    tcList: [
      {
        teaching_class_id: `T${i}`,
        teacher_name: math ? `数学教师${i}` : `英语教师${i}`,
        teaching_place: `教学楼${i}教室`,
        course_index: String(i + 1),
        is_choose: "0",
        is_conflict: "0",
        is_full: "0",
        is_mooc: "0",
        number_of_selected: 10,
        class_capacity: 100,
      },
    ],
  });
}

let fakeSession = {
  logged_in: true,
  student_id: "20240001",
  batch_code: "B2024",
  batch_name: "正选",
  phase: "grab",
  automatic_enroll_allowed: true,
  task_running: false,
  relogin_status: "idle",
  relogin_in_progress: false,
};
const schoolRequests = [];
let reportedTotalCount = fakeCourses.length;
let totalCountByPage = null;
globalThis.fetch = async (url) => {
  const parsed = new URL(url, "http://course.test");
  const respond = (payload) => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload),
  });
  if (parsed.pathname === "/api/session") {
    return respond({ ...fakeSession });
  }
  if (parsed.pathname === "/api/courses/dblist") {
    return respond([]);
  }
  if (parsed.pathname === "/api/school/courses") {
    const type = parsed.searchParams.get("type");
    const page = Number(parsed.searchParams.get("page"));
    let rejected = false;
    if (schoolThrottle.rejectOncePages.has(page) && !schoolThrottle.rejectedOncePages.has(page)) {
      schoolThrottle.rejectedOncePages.add(page);
      rejected = true;
    } else if (schoolThrottle.rejectAlwaysPages.has(page)) {
      rejected = true;
    }
    schoolTimeline.push({ type, page, at: Date.now(), rejected });
    if (rejected) {
      return schoolRejectedResponse();
    }
    schoolRequests.push(`${type}:${page}`);
    const start = (page - 1) * PAGE_SIZE;
    return respond({
      total_count: totalCountByPage?.[page] ?? reportedTotalCount,
      courses: fakeCourses.slice(start, start + PAGE_SIZE),
      msg: "",
      is_error: false,
    });
  }
  return {
    ok: false,
    status: 404,
    text: async () => JSON.stringify({ message: "not found", error_code: "NOT_FOUND" }),
  };
};

const appSource = fs.readFileSync(process.argv[2], "utf8");
const scenarioSource = fs.readFileSync(process.argv[3], "utf8");
globalThis.__CATALOG_TIMING__ = {
  pageDelayMs: 40,
  pageMaxDelayMs: 120,
  throttleBackoffMs: 40,
  throttleBackoffMaxMs: 120,
};
eval(appSource + "\n;\n" + scenarioSource);
"""

SCENARIO_JS = r"""
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

(async () => {
  const app = {
    appState,
    appElements,
    applySessionData,
    fetchFullCatalog,
    handleSearchInput,
    loadSession,
    refreshCurrentView,
    setSession: (updates) => {
      fakeSession = { ...fakeSession, ...updates };
    },
    schoolRequests: () => schoolRequests.slice(),
    setSchoolThrottle: (config = {}) => {
      schoolThrottle.rejectOncePages = new Set(config.rejectOncePages || []);
      schoolThrottle.rejectedOncePages = new Set();
      schoolThrottle.rejectAlwaysPages = new Set(config.rejectAlwaysPages || []);
    },
    schoolTimeline: () => schoolTimeline.slice(),
    nodeById: (id) => nodesById.get(id),
  };

  let ready = false;
  for (let i = 0; i < 500; i += 1) {
    if (app.appState.session?.logged_in && app.appState.courseDataKey === "TJKC:1") {
      ready = true;
      break;
    }
    await wait(10);
  }
  if (!ready) throw new Error("initializeApp did not settle in time");

  const requestsAfterInit = app.schoolRequests();

  // 1) 搜索：应在整个目录上过滤，并按匹配结果重新分页
  app.appElements.courseSearch.value = "数学";
  await app.handleSearchInput();
  const searchState = {
    requests: app.schoolRequests().slice(),
    results: app.appState.searchResults.length,
    pageLabel: app.nodeById("pageLabel").textContent,
    renderedRows: app.nodeById("courseList").children.length,
    summary: app.nodeById("courseSummary").textContent,
    firstName: app.nodeById("courseList").children[0]?.children[0]?.children[0]?.children[0]?.textContent,
  };

  // 2) 筛选结果内翻页：不应再请求学校接口
  const requestCountBeforePaging = app.schoolRequests().length;
  app.nodeById("nextPage").click();
  const nextPageState = {
    pageLabel: app.nodeById("pageLabel").textContent,
    renderedRows: app.nodeById("courseList").children.length,
    noNewSchoolRequests: app.schoolRequests().length === requestCountBeforePaging,
  };
  app.nodeById("nextPage").click();
  const boundedNextPageState = {
    pageLabel: app.nodeById("pageLabel").textContent,
  };

  // 3) 清空关键词：回到服务端分页视图
  app.appElements.courseSearch.value = "";
  await app.handleSearchInput();
  const clearedState = {
    pageLabel: app.nodeById("pageLabel").textContent,
    renderedRows: app.nodeById("courseList").children.length,
    summary: app.nodeById("courseSummary").textContent,
  };

  // 4) 再次搜索：命中目录缓存，不再请求学校接口
  const requestCountBeforeCachedSearch = app.schoolRequests().length;
  app.appElements.courseSearch.value = "大学英语";
  await app.handleSearchInput();
  const cachedSearchState = {
    requests: app.schoolRequests().slice(),
    results: app.appState.searchResults.length,
    pageLabel: app.nodeById("pageLabel").textContent,
    renderedRows: app.nodeById("courseList").children.length,
    noNewSchoolRequests: app.schoolRequests().length === requestCountBeforeCachedSearch,
  };

  // 5) 普通刷新应清理目录缓存；下一次搜索必须重新读取全部页面
  app.appElements.courseSearch.value = "";
  await app.handleSearchInput();
  const requestCountBeforeRefresh = app.schoolRequests().length;
  await app.refreshCurrentView();
  const requestsFromRefresh = app.schoolRequests().slice(requestCountBeforeRefresh);
  const requestCountAfterRefresh = app.schoolRequests().length;
  app.appElements.courseSearch.value = "大学英语";
  await app.handleSearchInput();
  const refreshInvalidationState = {
    refreshRequests: requestsFromRefresh,
    searchRequests: app.schoolRequests().slice(requestCountAfterRefresh),
    results: app.appState.searchResults.length,
  };

  // 6) 学号或选课批次变化时，旧目录不能跨会话继续复用
  const requestCountBeforeScopeChange = app.schoolRequests().length;
  app.applySessionData({
    ...app.appState.session,
    batch_code: "B2025",
    batch_name: "补选",
  });
  const cacheRemovedOnScopeChange = !app.appState.catalogCaches.TJKC;
  await app.handleSearchInput();
  const scopeChangeState = {
    cacheRemoved: cacheRemovedOnScopeChange,
    searchRequests: app.schoolRequests().slice(requestCountBeforeScopeChange),
    results: app.appState.searchResults.length,
  };

  // 7) 自动重登录完成后，即使学号和批次未变，也要刷新动态课程状态
  const cacheBeforeRelogin = app.appState.catalogCaches.TJKC;
  const requestCountBeforeRelogin = app.schoolRequests().length;
  app.applySessionData({
    ...app.appState.session,
    relogin_status: "running",
    relogin_in_progress: true,
  });
  app.setSession({
    batch_code: "B2025",
    batch_name: "补选",
    relogin_status: "success",
    relogin_in_progress: false,
  });
  await app.loadSession(false);
  const reloginInvalidationState = {
    cacheReplaced: app.appState.catalogCaches.TJKC !== cacheBeforeRelogin,
    cacheComplete: Boolean(app.appState.catalogCaches.TJKC?.complete),
    searchRequests: app.schoolRequests().slice(requestCountBeforeRelogin),
    results: app.appState.searchResults.length,
  };

  // 8) 抢课运行中恢复登录时只提示手动刷新，避免自动全目录请求争用学校接口
  const requestCountBeforeRunningTaskRelogin = app.schoolRequests().length;
  app.applySessionData({
    ...app.appState.session,
    task_running: true,
    relogin_status: "running",
    relogin_in_progress: true,
  });
  app.setSession({
    task_running: true,
    relogin_status: "success",
    relogin_in_progress: false,
  });
  await app.loadSession(false);
  const runningTaskReloginState = {
    noNewSchoolRequests: app.schoolRequests().length === requestCountBeforeRunningTaskRelogin,
    cacheRemoved: !app.appState.catalogCaches.TJKC,
    summary: app.appElements.courseSummary.textContent,
  };

  // 9) 异常总页数必须明确失败，不能把截断目录标记为完整缓存
  reportedTotalCount = FILTER_PAGE_SIZE * (MAX_CATALOG_PAGES + 1);
  const requestCountBeforeOversizedCatalog = app.schoolRequests().length;
  const oversizedOk = await app.fetchFullCatalog({ force: true });
  const oversizedCatalogState = {
    ok: oversizedOk,
    requests: app.schoolRequests().slice(requestCountBeforeOversizedCatalog),
    hasCompleteCache: Boolean(app.appState.catalogCaches.TJKC?.complete),
    refreshEnabled: !app.appElements.refreshCourses.disabled,
  };

  // 10) 分页期间总数变化时不能拼接来自不同快照的课程
  reportedTotalCount = fakeCourses.length;
  totalCountByPage = { 1: fakeCourses.length, 2: fakeCourses.length - 1 };
  const requestCountBeforeChangingTotal = app.schoolRequests().length;
  const changingTotalOk = await app.fetchFullCatalog({ force: true });
  const changingTotalState = {
    ok: changingTotalOk,
    requests: app.schoolRequests().slice(requestCountBeforeChangingTotal),
    hasCompleteCache: Boolean(app.appState.catalogCaches.TJKC?.complete),
  };

  // 11) 学校限流拒绝一次：自动退避重试同一页并完成目录；分页间隔随之加倍
  totalCountByPage = null;
  app.setSchoolThrottle({ rejectOncePages: [2] });
  const requestCountBeforeThrottled = app.schoolRequests().length;
  const timelineBeforeThrottled = app.schoolTimeline().length;
  app.appState.type = "XGXK";
  await app.handleSearchInput();
  const xgxkTimeline = app.schoolTimeline().slice(timelineBeforeThrottled);
  const xgxkGaps = xgxkTimeline.slice(1).map((item, index) => item.at - xgxkTimeline[index].at);
  const rejectedIndex = xgxkTimeline.findIndex((item) => item.rejected);
  const throttledRetryState = {
    requests: app.schoolRequests().slice(requestCountBeforeThrottled),
    results: app.appState.searchResults.length,
    pageLabel: app.nodeById("pageLabel").textContent,
    hasCompleteCache: Boolean(app.appState.catalogCaches.XGXK?.complete),
    rejectedCount: xgxkTimeline.filter((item) => item.rejected).length,
    minGapMs: Math.min(...xgxkGaps),
    gapAfterRetry: rejectedIndex >= 0 ? (xgxkGaps[rejectedIndex + 1] ?? -1) : -1,
    gaps: xgxkGaps,
  };

  // 12) 学校持续限流：重试次数耗尽后整体失败，不留下伪完整缓存
  app.setSchoolThrottle({ rejectAlwaysPages: [2] });
  app.appState.type = "TYKC";
  const attemptsBeforeExhausted = app.schoolTimeline().filter(
    (item) => item.type === "TYKC" && item.page === 2,
  ).length;
  const exhaustedOk = await app.fetchFullCatalog({ force: true });
  const tykcAttemptsPage2 = app.schoolTimeline().filter(
    (item) => item.type === "TYKC" && item.page === 2,
  ).length - attemptsBeforeExhausted;
  const throttleExhaustedState = {
    ok: exhaustedOk,
    tykcAttemptsPage2,
    hasCache: Boolean(app.appState.catalogCaches.TYKC),
    errorTitle: app.nodeById("courseList").children[0]?.children[0]?.textContent,
    toast: app.nodeById("toastRegion").children.at(-1)?.textContent,
  };
  app.setSchoolThrottle();

  console.log(JSON.stringify({
    requestsAfterInit,
    searchState,
    nextPageState,
    boundedNextPageState,
    clearedState,
    cachedSearchState,
    refreshInvalidationState,
    scopeChangeState,
    reloginInvalidationState,
    runningTaskReloginState,
    oversizedCatalogState,
    changingTotalState,
    throttledRetryState,
    throttleExhaustedState,
  }));
  process.exit(0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required to run the UI harness")
def test_course_search_filters_whole_catalog_and_repaginates(tmp_path: Path) -> None:
    harness = tmp_path / "harness.js"
    scenario = tmp_path / "scenario.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    scenario.write_text(SCENARIO_JS, encoding="utf-8")

    proc = subprocess.run(
        ["node", str(harness), str(APP_JS), str(scenario)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    # 初始化只加载第 1 页
    assert out["requestsAfterInit"] == ["TJKC:1"]

    # 搜索在全部 4 页上过滤出 12 门数学课程，并重新分页为 2 页
    search = out["searchState"]
    assert sorted(set(search["requests"])) == ["TJKC:1", "TJKC:2", "TJKC:3", "TJKC:4"]
    assert search["results"] == 12
    assert search["pageLabel"] == "第 1 / 2 页"
    assert search["renderedRows"] == 10
    assert "匹配 12 门课程" in search["summary"]
    assert "数学" in (search["firstName"] or "")

    # 筛选结果内翻页为客户端行为，不再请求学校接口
    next_page = out["nextPageState"]
    assert next_page["pageLabel"] == "第 2 / 2 页"
    assert next_page["renderedRows"] == 2
    assert next_page["noNewSchoolRequests"] is True
    # 超出末页后停在最后一页
    assert out["boundedNextPageState"]["pageLabel"] == "第 2 / 2 页"

    # 清空关键词回到服务端分页（35 门 → 4 页）
    cleared = out["clearedState"]
    assert cleared["pageLabel"] == "第 1 / 4 页"
    assert cleared["renderedRows"] == 10
    assert "共 35 门课程" in cleared["summary"]

    # 目录已缓存：再次搜索零学校请求
    cached = out["cachedSearchState"]
    assert cached["noNewSchoolRequests"] is True
    assert cached["results"] == 23
    assert cached["pageLabel"] == "第 1 / 3 页"
    assert cached["renderedRows"] == 10

    # 普通刷新只取当前服务端页，但会清理全目录缓存；随后搜索重新读取 4 页
    refreshed = out["refreshInvalidationState"]
    assert refreshed["refreshRequests"] == ["TJKC:1"]
    assert refreshed["searchRequests"] == ["TJKC:1", "TJKC:2", "TJKC:3", "TJKC:4"]
    assert refreshed["results"] == 23

    # 批次切换和重登录成功都必须使缓存失效，并重新读取完整目录
    scope_changed = out["scopeChangeState"]
    assert scope_changed["cacheRemoved"] is True
    assert scope_changed["searchRequests"] == ["TJKC:1", "TJKC:2", "TJKC:3", "TJKC:4"]
    assert scope_changed["results"] == 23

    relogged = out["reloginInvalidationState"]
    assert relogged["cacheReplaced"] is True
    assert relogged["cacheComplete"] is True
    assert relogged["searchRequests"] == ["TJKC:1", "TJKC:2", "TJKC:3", "TJKC:4"]
    assert relogged["results"] == 23

    running_relogin = out["runningTaskReloginState"]
    assert running_relogin["noNewSchoolRequests"] is True
    assert running_relogin["cacheRemoved"] is True
    assert running_relogin["summary"] == "课程状态需要重新加载"

    # 超过安全页数时立刻失败，只请求首屏且不留下伪完整缓存
    oversized = out["oversizedCatalogState"]
    assert oversized["ok"] is False
    assert oversized["requests"] == ["TJKC:1"]
    assert oversized["hasCompleteCache"] is False
    assert oversized["refreshEnabled"] is True

    changing_total = out["changingTotalState"]
    assert changing_total["ok"] is False
    assert changing_total["requests"] == ["TJKC:1", "TJKC:2"]
    assert changing_total["hasCompleteCache"] is False

    # 学校限流拒绝一次：退避后重试同一页并完整加载；分页间隔自动加倍
    throttled = out["throttledRetryState"]
    assert throttled["requests"] == ["XGXK:1", "XGXK:2", "XGXK:3", "XGXK:4"]
    assert throttled["rejectedCount"] == 1
    assert throttled["hasCompleteCache"] is True
    assert throttled["results"] == 23
    assert throttled["pageLabel"] == "第 1 / 3 页"
    assert throttled["minGapMs"] >= 30, throttled["gaps"]
    # 被限流页重试成功后，下一页间隔应为基础间隔的 2 倍（40ms → 80ms）
    assert throttled["gapAfterRetry"] >= 70, throttled["gaps"]

    # 持续限流：初始请求 + 3 次重试全部被拒后整体失败，不留下缓存
    exhausted = out["throttleExhaustedState"]
    assert exhausted["ok"] is False
    assert exhausted["tykcAttemptsPage2"] == 4
    assert exhausted["hasCache"] is False
    # 失败后必须脱离加载态：渲染带重试入口的错误态，并通过 toast 说明限流原因
    assert exhausted["errorTitle"] == "课程目录尚未加载"
    assert "学校暂时拒绝了请求" in (exhausted["toast"] or "")
