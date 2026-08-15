"use strict";

/**
 * SZU 教学时间解析模块
 *
 * 解析学校 teachingPlace 字段（如 "1-18周 星期三 9-10节 汇文楼H2-303"），
 * 提取周数、星期、节次、地点。
 * 周表网格按单节课（第 1 节 ~ 第 14 节）划分，一门课连占多节时由前端跨行显示。
 */

/* SZU 标准作息（已从教务处课表图片确认）：每节课的上课开始时间 */
const PERIODS = [
  { period: 1, timeLabel: "8:30" },
  { period: 2, timeLabel: "9:15" },
  { period: 3, timeLabel: "10:15" },
  { period: 4, timeLabel: "11:00" },
  { period: 5, timeLabel: "11:45" },
  { period: 6, timeLabel: "13:30" },
  { period: 7, timeLabel: "14:15" },
  { period: 8, timeLabel: "15:00" },
  { period: 9, timeLabel: "16:00" },
  { period: 10, timeLabel: "16:45" },
  { period: 11, timeLabel: "19:00" },
  { period: 12, timeLabel: "19:45" },
  { period: 13, timeLabel: "20:30" },
  { period: 14, timeLabel: "21:15" },
];

const MAX_PERIOD = 14;

/* 标准课序对（两节一连），供归组显示与兼容判断使用 */
const TIME_SLOTS = [
  { label: "1-2节", startPeriod: 1, endPeriod: 2, timeLabel: "8:30-9:55" },
  { label: "3-4节", startPeriod: 3, endPeriod: 4, timeLabel: "10:15-11:40" },
  { label: "5-6节", startPeriod: 5, endPeriod: 6, timeLabel: "11:45-14:10" },
  { label: "7-8节", startPeriod: 7, endPeriod: 8, timeLabel: "14:15-15:40" },
  { label: "9-10节", startPeriod: 9, endPeriod: 10, timeLabel: "16:00-17:25" },
  { label: "11-12节", startPeriod: 11, endPeriod: 12, timeLabel: "19:00-20:20" },
  { label: "13-14节", startPeriod: 13, endPeriod: 14, timeLabel: "20:30-21:45" },
];

const DAYS_OF_WEEK = [
  { label: "星期一", shortLabel: "一" },
  { label: "星期二", shortLabel: "二" },
  { label: "星期三", shortLabel: "三" },
  { label: "星期四", shortLabel: "四" },
  { label: "星期五", shortLabel: "五" },
  { label: "星期六", shortLabel: "六" },
  { label: "星期日", shortLabel: "日" },
];

const DAY_CHAR_MAP = {
  "一": 0,
  "二": 1,
  "三": 2,
  "四": 3,
  "五": 4,
  "六": 5,
  "日": 6,
  "天": 6,
};

/* 周数正则：如 "1-18周" 或 "2-4周(双)" 或 "3-4周,7-8周,10-11周"，容忍空格与、分隔 */
const WEEKS_REGEX = /((?:\d+\s*-\s*\d+\s*周(?:\([单双]\))?)(?:[,，、]\s*\d+\s*-\s*\d+\s*周(?:\([单双]\))?)*)/g;

/* 星期正则：匹配 "星期X" 或 "周X"（X 为一二三四五六日天） */
const DAY_REGEX = /(?:星期|周)([一二三四五六日天])/g;

/* 节次正则：如 "9-10节"、"第9-10节"、"9 - 10 节"，也支持单节 "5节" */
const PERIOD_REGEX = /第?\s*(\d+)\s*(?:[-–~]\s*(\d+))?\s*节/g;

/**
 * 将周数字符串解析为范围数组。
 * "1-18周" -> [[1, 18]]
 * "2-4周,6-10周(双)" -> [[2, 4], [6, 10]]
 */
function parseWeekRanges(weeksStr) {
  if (!weeksStr) return [];
  const ranges = [];
  const parts = weeksStr.split(/[,，、]/);
  for (const part of parts) {
    const match = part.trim().match(/^(\d+)\s*-\s*(\d+)\s*周/);
    if (match) {
      ranges.push([parseInt(match[1], 10), parseInt(match[2], 10)]);
    }
  }
  return ranges;
}

/**
 * 查找节次对应的标准课序对（TIME_SLOTS）索引。
 * 返回索引或 null。
 */
function findSlotIndex(startPeriod, endPeriod) {
  for (let i = 0; i < TIME_SLOTS.length; i++) {
    const slot = TIME_SLOTS[i];
    if (startPeriod >= slot.startPeriod && endPeriod <= slot.endPeriod) {
      return i;
    }
  }
  for (let i = 0; i < TIME_SLOTS.length; i++) {
    const slot = TIME_SLOTS[i];
    if (startPeriod >= slot.startPeriod && startPeriod <= slot.endPeriod) {
      return i;
    }
  }
  return null;
}

/**
 * 从一段文本中移除已匹配的周数、星期、节次片段，剩余文本作为地点。
 */
function extractPlaceFromSegment(segment, matchedTexts) {
  let place = segment;
  for (const text of matchedTexts) {
    place = place.replace(text, " ");
  }
  place = place.replace(/[,，、]/g, " ").replace(/\s+/g, " ").trim();
  return place || "";
}

/**
 * 收集原始字符串中所有匹配项及其位置（matchAll 结果的包装）。
 * 每项包含 text、index（起始位置）、end（结束位置）。
 */
function collectMatches(raw, regex, transform) {
  const results = [];
  for (const m of raw.matchAll(regex)) {
    results.push({
      text: m[0],
      index: m.index,
      end: m.index + m[0].length,
      ...(transform ? transform(m) : {}),
    });
  }
  return results;
}

/**
 * 解析一条 teachingPlace 字符串，返回 ScheduleSlot 数组。
 *
 * 一门课程可能有多个教学时间段（如不同周段不同时段），
 * 如果 teachingPlace 包含多个"星期X Y-Z节"片段，则返回多个 slot。
 * 多个时间段之间用逗号分隔时，每个时间段独立提取地点。
 *
 * 每个返回项：
 * - weeks: "1-18周" — 原始周数字符串（用于标注和颜色分组）
 * - weekRanges: [[1,18]] — 解析后的周数范围数组
 * - dayOfWeek: 2 — 0=周一 ... 6=周日
 * - dayLabel: "星期三"
 * - startPeriod: 9 — 起始节（单节课时与 endPeriod 相等）
 * - endPeriod: 10 — 结束节
 * - slotIndex: 4 — 所属标准课序对索引，null 表示不属于任何课序对
 * - place: "汇文楼H2-303"
 * - raw: 原始字符串
 */
function parseScheduleSlots(teachingPlace) {
  if (!teachingPlace || typeof teachingPlace !== "string") return [];

  const raw = teachingPlace.trim();
  if (!raw) return [];

  /* 收集所有匹配项及其位置 */
  const weeksItems = collectMatches(raw, WEEKS_REGEX);
  const dayItems = collectMatches(raw, DAY_REGEX, (m) => ({
    dayChar: m[1],
    dayOfWeek: DAY_CHAR_MAP[m[1]],
  }));
  const periodItems = collectMatches(raw, PERIOD_REGEX, (m) => ({
    start: parseInt(m[1], 10),
    end: m[2] ? parseInt(m[2], 10) : parseInt(m[1], 10),
  }));

  /* 如果没有星期或节次，无法解析 */
  if (!dayItems.length || !periodItems.length) return [];

  /*
   * 将原始字符串拆分为若干时间段。
   * 每个时间段由一个周数（可选）、一个星期、一个节次组成。
   * 按位置顺序配对：每个 dayItem 找到其后最近的 periodItem，
   * 以及其前最近的 weeksItem（或前一个时间段的 weeksItem）。
   */
  const slots = [];
  const usedPeriods = new Set();

  for (let di = 0; di < dayItems.length; di++) {
    const dayItem = dayItems[di];

    /* 找到这个星期之后最近的未使用节次 */
    let periodItem = null;
    for (let pi = 0; pi < periodItems.length; pi++) {
      if (usedPeriods.has(pi)) continue;
      if (periodItems[pi].index >= dayItem.index) {
        periodItem = periodItems[pi];
        usedPeriods.add(pi);
        break;
      }
    }
    if (!periodItem) {
      /* 没有找到配对的节次，尝试任意未使用的 */
      for (let pi = 0; pi < periodItems.length; pi++) {
        if (!usedPeriods.has(pi)) {
          periodItem = periodItems[pi];
          usedPeriods.add(pi);
          break;
        }
      }
    }
    if (!periodItem) continue;

    /* 找到这个星期之前最近的周数（如果没有则用前一个时间段的） */
    let weeksStr = "";
    for (let wi = weeksItems.length - 1; wi >= 0; wi--) {
      if (weeksItems[wi].index < dayItem.index) {
        weeksStr = weeksItems[wi].text;
        break;
      }
    }

    /*
     * 确定这个时间段的文本范围：从 weeksItem（或 dayItem）开始
     * 到 periodItem 结束之后，到下一个 dayItem（或下一个 weeksItem）之前。
     * 从这段文本中提取地点。
     */
    const segStart = weeksStr
      ? Math.min(
          ...weeksItems.filter((w) => w.text === weeksStr && w.index < dayItem.index).map((w) => w.index),
          dayItem.index,
        )
      : dayItem.index;

    let segEnd = raw.length;
    /* 下一个 dayItem 的位置 */
    if (di + 1 < dayItems.length) {
      segEnd = Math.min(segEnd, dayItems[di + 1].index);
    }
    /* 下一个 weeksItem 在 periodItem 之后的位置 */
    for (const wi of weeksItems) {
      if (wi.index > periodItem.end && wi.index < segEnd) {
        segEnd = wi.index;
      }
    }

    const segment = raw.substring(segStart, segEnd);
    const segMatched = [dayItem.text, periodItem.text];
    if (weeksStr) {
      const wi = weeksItems.find((w) => w.text === weeksStr && w.index >= segStart && w.index < dayItem.index);
      if (wi) segMatched.push(wi.text);
    }
    const place = extractPlaceFromSegment(segment, segMatched);

    const slotIndex = findSlotIndex(periodItem.start, periodItem.end);

    slots.push({
      weeks: weeksStr,
      weekRanges: parseWeekRanges(weeksStr),
      dayOfWeek: dayItem.dayOfWeek,
      dayLabel: DAYS_OF_WEEK[dayItem.dayOfWeek]?.label || dayItem.text,
      startPeriod: periodItem.start,
      endPeriod: periodItem.end,
      slotIndex,
      place,
      raw,
    });
  }

  return slots;
}
