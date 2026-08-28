/**
 * Тесты чистых функций Worker'а: node --test worker/
 *
 * Проверяем ровно то, что нельзя проверить глазами - совпадение формата и
 * правил слияния с питоновским сборщиком. Сеть и GitHub не трогаем.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  closedHours,
  formatLength,
  formatLocal,
  hoursWord,
  inspect,
  lastMeasurement,
  watchMessage,
  formatScore,
  formatUtc,
  isWindowOpen,
  parseCsv,
  serializeCsv,
  toIrkutsk,
  upsert,
} from "./traffic-worker.js";

const HEADER =
  "ts_utc,ts_local,date,hour,source,score,hint,jams_length,error";

function row(overrides = {}) {
  return {
    ts_utc: "2026-08-28T04:00:12+00:00",
    ts_local: "2026-08-28T12:00:12",
    date: "2026-08-28",
    hour: "12",
    source: "yandex",
    score: "3",
    hint: "Местами затруднения",
    jams_length: "24728.3",
    error: "",
    ...overrides,
  };
}

test("местное время - это UTC плюс восемь часов", () => {
  const local = toIrkutsk(new Date("2026-08-27T23:30:00Z"));
  assert.equal(formatLocal(local), "2026-08-28T07:30:00");
  assert.equal(local.getUTCHours(), 7);
});

test("метки времени пишутся как у питоновского сборщика", () => {
  const now = new Date("2026-08-28T04:00:12.987Z");
  assert.equal(formatUtc(now), "2026-08-28T04:00:12+00:00");
  assert.equal(formatLocal(toIrkutsk(now)), "2026-08-28T12:00:12");
});

test("окно закрыто ночью и открыто в крайние часы", () => {
  const at = (hour) => toIrkutsk(new Date(Date.UTC(2026, 7, 28, hour - 8, 0, 0)));
  assert.equal(isWindowOpen(at(6)), false);
  assert.equal(isWindowOpen(at(7)), true);
  assert.equal(isWindowOpen(at(23)), true);
  assert.equal(isWindowOpen(at(0)), false);
});

test("балл печатается без хвоста .0, пустой - пустым", () => {
  assert.equal(formatScore(3), "3");
  assert.equal(formatScore(3.5), "3.5");
  assert.equal(formatScore(null), "");
});

test("длина пробок пишется с дробной частью, как str(float) в Python", () => {
  // В файле уже есть замеры вида 42527.0 - формат столбца не должен зависеть
  // от того, кто снял балл: Actions или Worker.
  assert.equal(formatLength(42527), "42527.0");
  assert.equal(formatLength(24728.3), "24728.3");
  assert.equal(formatLength(undefined), "");
});

test("источники сортируются по кодовым точкам, как в Python", () => {
  const { rows } = upsert([], [row({ source: "yandex" }), row({ source: "2gis", score: "1" })]);
  assert.deepEqual(rows.map((item) => item.source), ["2gis", "yandex"]);
});

test("разбор и сборка CSV не меняют файл", () => {
  const text = `${HEADER}\n${[
    "2026-08-27T05:00:17+00:00,2026-08-27T13:00:17,2026-08-27,13,2gis,1,,,",
    "2026-08-27T05:00:17+00:00,2026-08-27T13:00:17,2026-08-27,13,yandex,3,Местами затруднения,24728.3,",
  ].join("\n")}\n`;
  assert.equal(serializeCsv(parseCsv(text)), text);
});

test("ячейка с запятой и кавычкой переживает круг", () => {
  const tricky = row({ error: 'network: сбой, код "500"', score: "", hint: "" });
  const parsed = parseCsv(serializeCsv([tricky]));
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].error, 'network: сбой, код "500"');
});

test("пустой файл и один заголовок дают пустую историю", () => {
  assert.deepEqual(parseCsv(""), []);
  assert.deepEqual(parseCsv(`${HEADER}\n`), []);
});

test("строки с CRLF читаются без лишнего перевода строки", () => {
  const parsed = parseCsv(`${HEADER}\r\n${serializeCsv([row()]).split("\n")[1]}\r\n`);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].hint, "Местами затруднения");
});

test("удачный замер за час не перетирается повторным", () => {
  const existing = [row({ score: "3" })];
  const { rows, added, replaced } = upsert(existing, [row({ score: "7" })]);
  assert.equal(added, 0);
  assert.equal(replaced, 0);
  assert.equal(rows[0].score, "3");
});

test("строку с ошибкой удачный замер заменяет", () => {
  const existing = [row({ score: "", error: "network: таймаут" })];
  const { rows, added, replaced } = upsert(existing, [row({ score: "5" })]);
  assert.equal(added, 0);
  assert.equal(replaced, 1);
  assert.equal(rows[0].score, "5");
});

test("новый час дописывается, а история остаётся отсортированной", () => {
  const existing = [row({ hour: "9" }), row({ hour: "10" })];
  const { rows, added } = upsert(existing, [row({ hour: "8" })]);
  assert.equal(added, 1);
  assert.deepEqual(rows.map((item) => item.hour), ["8", "9", "10"]);
});

test("час сортируется числом, а не строкой", () => {
  const existing = [row({ hour: "9" })];
  const { rows } = upsert(existing, [row({ hour: "10" })]);
  assert.deepEqual(rows.map((item) => item.hour), ["9", "10"]);
});

test("два источника за один час не считаются дублем", () => {
  const { rows, added } = upsert([row({ source: "yandex" })], [row({ source: "2gis", score: "1" })]);
  assert.equal(added, 1);
  assert.equal(rows.length, 2);
});

/* ------------------------------- сторож ---------------------------------- */

// Местное время как объект, у которого UTC-поля равны иркутским.
const at = (hour, minute = 0) => toIrkutsk(new Date(Date.UTC(2026, 7, 28, hour - 8, minute, 0)));

// Ряд за сегодня: часы с 7 до hourTo включительно, оба источника.
function history(hourTo, overrides = {}) {
  const rows = [];
  for (let hour = 7; hour <= hourTo; hour += 1) {
    for (const source of ["2gis", "yandex"]) {
      rows.push(
        row({
          hour: String(hour),
          source,
          ts_local: `2026-08-28T${String(hour).padStart(2, "0")}:00:12`,
          ...overrides,
        }),
      );
    }
  }
  return rows;
}

test("склонение часов человеческое", () => {
  assert.equal(hoursWord(1), "час");
  assert.equal(hoursWord(3), "часа");
  assert.equal(hoursWord(11), "часов");
  assert.equal(hoursWord(21), "час");
});

test("последний замер берётся по удачным строкам, не по всем", () => {
  const rows = [...history(9), row({ hour: "10", score: "", error: "network", ts_local: "2026-08-28T10:00:12" })];
  assert.equal(formatLocal(new Date(lastMeasurement(rows))), "2026-08-28T09:00:12");
});

test("закрытые часы считают час один раз, даже если источников два", () => {
  assert.equal(closedHours(history(10), "2026-08-28"), 4); // 7, 8, 9, 10
});

test("свежий сбор проблем не даёт", () => {
  assert.equal(inspect(history(12), at(12, 30)).ok, true);
});

test("молчание дольше двух часов внутри окна - тревога", () => {
  const health = inspect(history(9), at(13));
  assert.equal(health.ok, false);
  assert.match(health.problems[0], /сбор молчит 4\.0 ч/);
});

test("ночью молчание законно и тревоги не даёт", () => {
  // 3:00 - окно закрыто, последний замер был днём: это норма, а не поломка.
  assert.equal(inspect(history(9), at(3)).ok, true);
});

test("три неудачных часа у источника за сутки - тревога", () => {
  const rows = [
    ...history(12),
    ...[13, 14, 15].map((hour) =>
      row({ hour: String(hour), source: "2gis", score: "", error: "network: таймаут", ts_local: `2026-08-28T${hour}:00:12` }),
    ),
  ];
  const health = inspect(rows, at(15, 30));
  assert.ok(health.problems.some((p) => p.includes("2gis") && p.includes("3 неудачных часа")));
});

test("одиночный сбой источника тревогой не считается", () => {
  const rows = [...history(12), row({ hour: "13", source: "2gis", score: "", error: "network", ts_local: "2026-08-28T13:00:12" })];
  assert.equal(inspect(rows, at(13, 30)).ok, true);
});

test("тревога уходит раз в час, а не на каждом тике", () => {
  const broken = history(9);
  assert.ok(watchMessage(broken, at(13, 0)), "в начале часа пишем");
  assert.equal(watchMessage(broken, at(13, 20)), null, "в середине часа молчим");
  assert.equal(watchMessage(broken, at(13, 40)), null, "и в конце тоже");
});

test("вечером приходит сводка, даже когда всё хорошо", () => {
  const message = watchMessage(history(23), at(23, 0));
  assert.ok(message);
  assert.match(message, /день закрыт/);
  assert.match(message, /закрыто 17 часов из 17/);
});

test("когда всё хорошо и час не вечерний - не пишем вовсе", () => {
  assert.equal(watchMessage(history(12), at(12, 0)), null);
});
