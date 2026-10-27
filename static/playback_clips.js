/* Pure clip-navigation helpers shared by the browser and Node tests. */
"use strict";

const CLIP_DAY_SECONDS = 86400;

function daySeconds(text, day) {
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(
    String(text || ""),
  );
  if (!match) {
    return null;
  }
  const datePart = `${match[1]}-${match[2]}-${match[3]}`;
  if (datePart < day) {
    return 0;
  }
  if (datePart > day) {
    return CLIP_DAY_SECONDS;
  }
  return (
    Number(match[4]) * 3600 + Number(match[5]) * 60 + Number(match[6])
  );
}

function clipStarts(files, day) {
  const starts = [];
  if (!Array.isArray(files)) {
    return starts;
  }
  for (const file of files) {
    const start = daySeconds(file?.start, day);
    if (start !== null) {
      starts.push(start);
    }
  }
  return [...new Set(starts)].sort((left, right) => left - right);
}

function adjacentClipStart(starts, currentSeconds, direction) {
  if (!Array.isArray(starts) || starts.length === 0) {
    return null;
  }
  if (direction === "next") {
    for (const start of starts) {
      if (start > currentSeconds) {
        return start;
      }
    }
    return null;
  }
  let previous = null;
  for (const start of starts) {
    if (start < currentSeconds) {
      previous = start;
    } else {
      break;
    }
  }
  return previous;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { daySeconds, clipStarts, adjacentClipStart };
}
