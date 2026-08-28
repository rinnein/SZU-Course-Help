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

const schoolRequests = [];
globalThis.fetch = async (url) => {
  const parsed = new URL(url, "http://course.test");
  const respond = (payload) => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload),
  });
  if (parsed.pathname === "/api/session") {
    return respond({
      logged_in: true,
      student_id: "20240001",
      batch_code: "B2024",
      batch_name: "正选",
      phase: "grab",
      automatic_enroll_allowed: true,
      task_running: false,
      relogin_status: "idle",
      relogin_in_progress: false,
    });
  }
  if (parsed.pathname === "/api/courses/dblist") {
    return respond([]);
  }
  if (parsed.pathname === "/api/school/courses") {
    const type = parsed.searchParams.get("type");
    const page = Number(parsed.searchParams.get("page"));
    schoolRequests.push(`${type}:${page}`);
    const start = (page - 1) * PAGE_SIZE;
    return respond({
      total_count: fakeCourses.length,
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
eval(appSource + "\n;\n" + scenarioSource);
"""

SCENARIO_JS = r"""
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

(async () => {
  const app = {
    appState,
    appElements,
    handleSearchInput,
    schoolRequests: () => schoolRequests.slice(),
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

  console.log(JSON.stringify({
    requestsAfterInit,
    searchState,
    nextPageState,
    boundedNextPageState,
    clearedState,
    cachedSearchState,
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
