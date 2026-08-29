/**
 * Сбор балла пробок по Иркутску на Cloudflare Workers.
 *
 * Зачем: расписание GitHub Actions перестало порождать запуски (26-28.08.2026 -
 * 59 часов тишины при ~150 ожидаемых тиках), и починить это со своей стороны
 * нельзя. Cron-триггеры Cloudflare исполняются честно, поэтому «когда снимать
 * балл» решает Cloudflare, а GitHub остаётся только хранилищем.
 *
 * Что делает: раз в 20 минут в окне 7:00-23:00 по Иркутску опрашивает Яндекс и
 * 2ГИС и дописывает строку в data/traffic_irkutsk.csv через GitHub Contents API.
 * Внутри часа засчитывается первый удачный замер - остальные попытки нужны на
 * случай, если источник не ответил, и файл они не трогают (см. upsert).
 *
 * Формат строки и правила слияния повторяют collect.py и collector/storage.py.
 * Расходятся они только подписью коммита: Worker подписывается traffic-worker,
 * Actions - traffic-bot, чтобы в истории было видно, кто писал.
 *
 * Файл самодостаточный: вставляется в редактор Cloudflare целиком, сборка не
 * нужна. Именованные экспорты внизу - для тестов (worker/tests.mjs).
 *
 * Переменные окружения Worker'а:
 *   GITHUB_TOKEN - секрет, fine-grained токен с правом Contents: read/write
 *                  на репозиторий forsite-traffic. Только в Secrets, не в vars.
 *   GITHUB_REPO  - "владелец/репозиторий", по умолчанию наш
 *   DATA_PATH    - путь к CSV в репозитории
 *   TRIGGER_KEY  - секрет для ручного прогона через HTTP; не задан - ручной
 *                  прогон выключен, снаружи доступен только статус
 */

const DEFAULT_REPO = "jukovaannag-art/forsite-traffic";
const DEFAULT_DATA_PATH = "data/traffic_irkutsk.csv";

// Иркутск - UTC+8 круглый год, перевода часов нет.
const IRKUTSK_OFFSET_MIN = 8 * 60;
const WINDOW_START_HOUR = 7;
const WINDOW_END_HOUR = 23; // час 23 собираем, 23:00-23:59 - последний в окне

const FIELDNAMES = [
  "ts_utc",
  "ts_local",
  "date",
  "hour",
  "source",
  "score",
  "hint",
  "jams_length",
  "error",
];

const KEY = ["date", "hour", "source"];

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

const YANDEX_URL = "https://export.yandex.ru/bar/reginfo.xml?region=63&lang=ru";
const DGIS_URL =
  "https://jam.api.2gis.com/scores?view=103.929159524171,52.465392368426734," +
  "104.632284475829,52.11018763157327&z=11";
const DGIS_PROJECT = "irkutsk";

const DASHBOARD_URL = "https://forsite-traffic.streamlit.app";

const SOURCE_TIMEOUT_MS = 20000;
// Больше трёх попыток не имеет смысла: если чужой коммит перебивает нас трижды
// подряд, строка всё равно уедет со следующим тиком через 20 минут.
const PUT_ATTEMPTS = 3;

/* ------------------------------- время ---------------------------------- */

/** Местное время Иркутска как Date, у которого UTC-поля равны местным. */
function toIrkutsk(now) {
  return new Date(now.getTime() + IRKUTSK_OFFSET_MIN * 60 * 1000);
}

/** "2026-08-28T04:00:12+00:00" - как пишет datetime.isoformat() в Python. */
function formatUtc(now) {
  return now.toISOString().replace(/\.\d+Z$/, "+00:00");
}

/** "2026-08-28T12:00:12" - местное время без пояса, как ts_local у сборщика. */
function formatLocal(local) {
  return local.toISOString().replace(/\.\d+Z$/, "");
}

function localDate(local) {
  return formatLocal(local).slice(0, 10);
}

function isWindowOpen(local) {
  const hour = local.getUTCHours();
  return hour >= WINDOW_START_HOUR && hour <= WINDOW_END_HOUR;
}

/* ------------------------------- числа ---------------------------------- */

/** Как "%g" в Python: целое печатается без хвоста ".0". */
function formatScore(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  return String(value);
}

/**
 * Длина пробок пишется как str(float) в Python, то есть с обязательной дробной
 * частью: 42527.0, а не 42527. JS сам хвост не ставит - дописываем, иначе
 * колонка в одном и том же файле выглядела бы по-разному в зависимости от
 * того, кто снял замер.
 */
function formatLength(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  return Number.isInteger(value) ? `${value}.0` : String(value);
}

/* -------------------------------- CSV ----------------------------------- */

/** Разбор CSV с кавычками. Пустой вход - пустой список. */
function parseCsv(text) {
  const rows = [];
  let field = "";
  let record = [];
  let quoted = false;
  let started = false; // была ли хоть одна ячейка в текущей строке

  const endField = () => {
    record.push(field);
    field = "";
    started = false;
  };
  const endRecord = () => {
    endField();
    rows.push(record);
    record = [];
  };

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"' && !started) {
      quoted = true;
      started = true;
    } else if (char === ",") {
      endField();
    } else if (char === "\n") {
      endRecord();
    } else if (char === "\r") {
      // CRLF: сам перевод строки обработает следующий шаг
    } else {
      field += char;
      started = true;
    }
  }
  // Последняя строка без завершающего перевода строки тоже считается.
  if (field !== "" || record.length > 0) endRecord();

  if (rows.length === 0) return [];
  const header = rows[0];
  return rows
    .slice(1)
    .filter((cells) => cells.some((cell) => cell !== ""))
    .map((cells) => {
      const row = {};
      header.forEach((name, index) => {
        row[name] = cells[index] ?? "";
      });
      return row;
    });
}

function escapeCell(value) {
  const text = value === null || value === undefined ? "" : String(value);
  if (/[",\r\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

/**
 * Сериализация в тот же вид, что даёт csv.DictWriter сборщика: только "\n" в
 * конце строк. Иначе каждый чужой прогон переписывал бы весь файл целиком.
 */
function serializeCsv(rows) {
  const lines = [FIELDNAMES.join(",")];
  for (const row of rows) {
    lines.push(FIELDNAMES.map((name) => escapeCell(row[name])).join(","));
  }
  return `${lines.join("\n")}\n`;
}

/* ------------------------------- слияние -------------------------------- */

function rowKey(row) {
  return KEY.map((name) => String(row[name] ?? "")).join(" ");
}

/**
 * Добавляет строки, схлопывая дубли по (дата, час, источник).
 *
 * Правило то же, что в collector/storage.py: удачный замер за час не
 * перетирается повторным (первый ближе к началу часа), но строку с ошибкой
 * удачная заменяет - так поздний ретрай чинит дырку.
 */
function upsert(existing, newRows) {
  const merged = existing.slice();
  const index = new Map(merged.map((row, position) => [rowKey(row), position]));

  let added = 0;
  let replaced = 0;
  for (const row of newRows) {
    const key = rowKey(row);
    const position = index.get(key);
    if (position === undefined) {
      merged.push(row);
      index.set(key, merged.length - 1);
      added += 1;
      continue;
    }
    const oldIsBad = String(merged[position].score ?? "").trim() === "";
    const newIsGood = String(row.score ?? "").trim() !== "";
    if (oldIsBad && newIsGood) {
      merged[position] = row;
      replaced += 1;
    }
  }

  // Сравнение по кодовым точкам, а не localeCompare: питоновский сборщик
  // сортирует именно так, и порядок строк в файле должен совпадать до символа,
  // иначе первая же запись Worker'а перетасует историю и раздует диff.
  const byCode = (left, right) => (left < right ? -1 : left > right ? 1 : 0);
  merged.sort((left, right) => {
    const byDate = byCode(String(left.date), String(right.date));
    if (byDate !== 0) return byDate;
    const byHour = (Number(left.hour) || 0) - (Number(right.hour) || 0);
    if (byHour !== 0) return byHour;
    return byCode(String(left.source), String(right.source));
  });

  return { rows: merged, added, replaced };
}

/* ------------------------------ источники -------------------------------- */

async function getText(url, referer) {
  const response = await fetch(url, {
    headers: {
      "User-Agent": USER_AGENT,
      Referer: referer,
      "Accept-Language": "ru-RU,ru;q=0.9",
    },
    signal: AbortSignal.timeout(SOURCE_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return await response.text();
}

/** Балл Яндекс.Пробок из публичного XML. Регулярные выражения, а не парсер XML:
 *  в Workers нет DOMParser, а разметка ответа простая и стабильная. */
async function fetchYandex() {
  let raw;
  try {
    raw = await getText(YANDEX_URL, "https://yandex.ru/maps/");
  } catch (error) {
    return { source: "yandex", score: null, hint: "", extra: {}, error: `network: ${error}` };
  }

  const region = raw.match(/<traffic[^>]*>[\s\S]*?<region[^>]*>([\s\S]*?)<\/region>/);
  if (!region) {
    return { source: "yandex", score: null, hint: "", extra: {}, error: "parse: нет блока traffic/region" };
  }
  const block = region[1];

  const level = block.match(/<level>([^<]*)<\/level>/);
  if (!level || !level[1].trim()) {
    return { source: "yandex", score: null, hint: "", extra: {}, error: "parse: нет level" };
  }
  const score = Number(level[1].trim());
  if (!Number.isFinite(score)) {
    return { source: "yandex", score: null, hint: "", extra: {}, error: "parse: level не число" };
  }

  const hintMatch = block.match(/<hint[^>]*lang="ru"[^>]*>([^<]*)<\/hint>/);
  const hint = hintMatch ? hintMatch[1].trim() : "";

  const extra = {};
  const length = block.match(/<length>([^<]*)<\/length>/);
  if (length && Number.isFinite(Number(length[1].trim()))) {
    extra.jams_length = Number(length[1].trim());
  }

  return { source: "yandex", score, hint, extra, error: "" };
}

/** Балл 2ГИС из того же эндпоинта, что использует 2gis.ru. Имя проекта
 *  проверяем, чтобы не записать балл чужого города. */
async function fetchDgis() {
  let raw;
  try {
    raw = await getText(DGIS_URL, "https://2gis.ru/irkutsk?traffic");
  } catch (error) {
    return { source: "2gis", score: null, hint: "", extra: {}, error: `network: ${error}` };
  }

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (error) {
    return { source: "2gis", score: null, hint: "", extra: {}, error: `parse: ${error}` };
  }

  const projects = payload.projects || [];
  const project = projects.find((item) => item && item.name === DGIS_PROJECT);
  if (!project) {
    const names = projects.map((item) => String(item && item.name)).join(", ") || "пусто";
    return { source: "2gis", score: null, hint: "", extra: {}, error: `parse: нет проекта ${DGIS_PROJECT} (пришло: ${names})` };
  }
  const score = Number(project.score);
  if (project.score === null || project.score === undefined || !Number.isFinite(score)) {
    return { source: "2gis", score: null, hint: "", extra: {}, error: "parse: нет score" };
  }
  return { source: "2gis", score, hint: "", extra: {}, error: "" };
}

/** Опрашивает оба источника параллельно и превращает ответы в строки CSV. */
async function collect(now) {
  const local = toIrkutsk(now);
  const readings = await Promise.all([fetchYandex(), fetchDgis()]);
  return readings.map((reading) => ({
    ts_utc: formatUtc(now),
    ts_local: formatLocal(local),
    date: localDate(local),
    hour: String(local.getUTCHours()),
    source: reading.source,
    score: formatScore(reading.score),
    hint: reading.hint,
    jams_length: formatLength(reading.extra.jams_length),
    error: reading.error,
  }));
}

/* -------------------------------- GitHub --------------------------------- */

function decodeBase64(base64) {
  const binary = atob(base64.replace(/\s/g, ""));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function encodeBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  // По кускам: String.fromCharCode(...bytes) на большом файле переполняет стек.
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function githubHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "forsite-traffic-worker",
  };
}

function contentsUrl(env) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  const path = env.DATA_PATH || DEFAULT_DATA_PATH;
  return `https://api.github.com/repos/${repo}/contents/${path}`;
}

/** Текущее содержимое CSV и его sha. Файла нет - пустая история. */
async function readCsv(env) {
  // Метка времени в запросе: без неё GitHub может отдать содержимое из кэша,
  // и upsert будет считать час незакрытым.
  const response = await fetch(`${contentsUrl(env)}?ref=main&t=${Date.now()}`, {
    headers: githubHeaders(env),
    cache: "no-store",
  });
  if (response.status === 404) return { rows: [], sha: null };
  if (!response.ok) {
    throw new Error(`GitHub отдал ${response.status} на чтение CSV: ${await response.text()}`);
  }
  const payload = await response.json();
  if (payload.content === undefined || payload.content === "") {
    // Файлы больше мегабайта Contents API отдаёт без содержимого. Наш ряд
    // дорастёт до этого примерно за год - тогда файл придётся делить по годам.
    throw new Error("GitHub не отдал содержимое: файл перерос лимит Contents API");
  }
  return { rows: parseCsv(decodeBase64(payload.content)), sha: payload.sha };
}

async function writeCsv(env, text, sha, message) {
  const response = await fetch(contentsUrl(env), {
    method: "PUT",
    headers: { ...githubHeaders(env), "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      content: encodeBase64(text),
      sha: sha ?? undefined,
      branch: "main",
      committer: {
        name: "traffic-worker",
        email: "traffic-bot@users.noreply.github.com",
      },
    }),
  });
  return response;
}

/* ------------------------------- сторож ---------------------------------- */

// Пороги те же, что в collector/health.py - проверка одна и та же, меняется
// только способ доставки. Два часа молчания, а не один: между замерами бывает
// законная пауза. Три неудачных часа у источника, а не один: одиночный сбой
// чинится следующим опросом.
const MAX_SILENCE_HOURS = 2;
const SOURCE_FAILURES_ALERT = 3;

/** «1 час», «3 часа», «5 часов» - иначе сообщение читается как лог. */
function hoursWord(count) {
  const tailTwo = count % 100;
  const tailOne = count % 10;
  if (tailTwo >= 11 && tailTwo <= 14) return "часов";
  if (tailOne === 1) return "час";
  if (tailOne >= 2 && tailOne <= 4) return "часа";
  return "часов";
}

/** Момент последнего удачного замера. Строки с ошибкой не считаются. */
function lastMeasurement(rows) {
  const stamps = rows
    .filter((row) => String(row.score ?? "").trim() !== "")
    .map((row) => Date.parse(`${row.ts_local}Z`))
    .filter((value) => Number.isFinite(value));
  return stamps.length > 0 ? Math.max(...stamps) : null;
}

/** Сколько часов за последние сутки источник так и не закрыл. */
function failuresBySource(rows, local) {
  const since = local.getTime() - 24 * 3600 * 1000;
  const counts = {};
  for (const row of rows) {
    const stamp = Date.parse(`${row.ts_local}Z`);
    if (!Number.isFinite(stamp) || stamp < since) continue;
    if (String(row.score ?? "").trim() !== "") continue;
    const source = String(row.source || "?");
    counts[source] = (counts[source] || 0) + 1;
  }
  return counts;
}

/** Сколько часов окна закрыто хотя бы одним источником за указанный день. */
function closedHours(rows, day) {
  const hours = new Set(
    rows
      .filter((row) => row.date === day && String(row.score ?? "").trim() !== "")
      .map((row) => Number(row.hour))
      .filter((hour) => hour >= WINDOW_START_HOUR && hour <= WINDOW_END_HOUR),
  );
  return hours.size;
}

/**
 * Проверка здоровья сбора: тревоги отдельно, цифры для сводки отдельно.
 * Вне окна молчание законно, поэтому ночью тревог не бывает - иначе каждое
 * утро приходило бы сообщение про восьмичасовую паузу.
 */
function inspect(rows, local) {
  const problems = [];
  const notes = [];

  if (isWindowOpen(local)) {
    const last = lastMeasurement(rows);
    if (last === null) {
      problems.push("в файле нет ни одного удачного замера");
    } else {
      const silenceHours = (local.getTime() - last) / 3600000;
      if (silenceHours > MAX_SILENCE_HOURS) {
        const when = formatLocal(new Date(last)).replace("T", " ").slice(5, 16);
        problems.push(
          `сбор молчит ${silenceHours.toFixed(1)} ч - последний замер ${when}, сейчас рабочее время`,
        );
      }
    }
  }

  const failures = failuresBySource(rows, local);
  for (const source of Object.keys(failures).sort()) {
    const count = failures[source];
    if (count >= SOURCE_FAILURES_ALERT) {
      problems.push(`источник ${source}: ${count} неудачных ${hoursWord(count)} за сутки`);
    }
  }

  const total = WINDOW_END_HOUR - WINDOW_START_HOUR + 1;
  const closed = closedHours(rows, localDate(local));
  notes.push(`за сегодня закрыто ${closed} ${hoursWord(closed)} из ${total}`);

  const last = lastMeasurement(rows);
  notes.push(
    last === null
      ? "замеров нет вовсе"
      : `последний замер: ${formatLocal(new Date(last)).replace("T", " ").slice(5, 16)}`,
  );

  const listing = Object.keys(failures)
    .sort()
    .map((name) => `${name} - ${failures[name]}`)
    .join(", ");
  notes.push(listing ? `неудачных часов за сутки: ${listing}` : "источники отвечали без сбоев");

  return { ok: problems.length === 0, problems, notes };
}

/**
 * Отправка в Telegram. Секретов нет - молчим: сторож не должен ронять сбор.
 * Ошибка отправки тоже не ронять: данные важнее уведомления.
 */
async function sendTelegram(env, text) {
  if (!env.TELEGRAM_TOKEN || !env.TELEGRAM_CHAT_ID) return "нет доступов, не отправлено";
  try {
    const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text,
        disable_web_page_preview: true,
      }),
      signal: AbortSignal.timeout(SOURCE_TIMEOUT_MS),
    });
    if (response.ok) return "отправлено";
    // Текст ошибки Telegram - это диагноз («chat not found», «unauthorized»),
    // и токена в нём нет. Без него причина неотправки невидима.
    const detail = (await response.text()).slice(0, 200);
    return `Telegram отдал ${response.status}: ${detail}`;
  } catch (error) {
    return `не отправилось: ${error}`;
  }
}

/**
 * Заводит issue в репозитории. GitHub сам шлёт письмо на почту владельца -
 * доставка без единого нового сервиса, пароля и бота.
 *
 * Открытых issue про поломку держим не больше одной: пока прошлая не закрыта,
 * новых не заводим, иначе за день отказа набралось бы полтора десятка писем.
 * Закрывает их человек - это и есть отметка «увидел». Токену закрывать нельзя,
 * и метки ставить тоже: проверено, GitHub отвечает 403. Поэтому свою issue
 * сторож узнаёт по заголовку.
 */
async function openIssue(env, title, body) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  const headers = githubHeaders(env);
  try {
    const existing = await fetch(
      `https://api.github.com/repos/${repo}/issues?state=open&per_page=50&t=${Date.now()}`,
      { headers, cache: "no-store" },
    );
    if (existing.ok) {
      const items = await existing.json();
      const same = Array.isArray(items) && items.find((item) => item.title === title);
      if (same) return `уже открыт #${same.number}, второй не заводим`;
    }
    const created = await fetch(`https://api.github.com/repos/${repo}/issues`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ title, body }),
    });
    if (!created.ok) return `GitHub отдал ${created.status} на создание issue`;
    const issue = await created.json();
    return `заведён #${issue.number}`;
  } catch (error) {
    return `не получилось: ${error}`;
  }
}

/**
 * Решает, писать ли сейчас и что именно.
 *
 * Тревоги - только на тике в начале часа: проверка идёт каждые 20 минут, и без
 * этого при поломке приходило бы по три сообщения в час. Сводка - в последний
 * час окна, она же подтверждение, что сторож жив.
 */
function watchMessage(rows, local) {
  const health = inspect(rows, local);
  const isSummaryHour = local.getUTCHours() === WINDOW_END_HOUR;
  const isTopOfHour = local.getUTCMinutes() < 20;

  const problems = health.problems.map((p) => `- ${p}`);
  const notes = health.notes.map((n) => `- ${n}`);

  if (isSummaryHour && isTopOfHour) {
    const head = health.ok ? "Пробки Иркутска: день закрыт" : "Пробки Иркутска: день закрыт с проблемами";
    const text = [head, "", ...problems, ...(problems.length ? [""] : []), ...notes, "", DASHBOARD_URL].join("\n");
    return { kind: "summary", title: head, text };
  }
  if (!health.ok && isTopOfHour) {
    const head = "Пробки Иркутска: похоже, сбор сломался";
    const text = [head, "", ...problems, "", ...notes, "", DASHBOARD_URL].join("\n");
    return { kind: "alert", title: head, text };
  }
  return null;
}

/* --------------------------------- ход ----------------------------------- */

/**
 * Один тик: опросить источники и, если час ещё не закрыт, записать строки.
 * Возвращает короткий отчёт - он же тело ответа при ручном прогоне.
 */
async function tick(env, now = new Date()) {
  if (!env.GITHUB_TOKEN) throw new Error("Нет GITHUB_TOKEN - писать в репозиторий нечем");

  const local = toIrkutsk(now);
  if (!isWindowOpen(local)) {
    return { skipped: `окно закрыто (${local.getUTCHours()}:00 по Иркутску)` };
  }

  const rows = await collect(now);
  const answered = rows.filter((row) => row.score !== "").map((row) => `${row.source}=${row.score}`);

  for (let attempt = 1; attempt <= PUT_ATTEMPTS; attempt += 1) {
    const { rows: existing, sha } = await readCsv(env);
    const { rows: merged, added, replaced } = upsert(existing, rows);
    if (added === 0 && replaced === 0) {
      return { written: false, answered, note: `час ${local.getUTCHours()}:00 уже закрыт`, history: merged };
    }

    const stamp = `${localDate(local)} ${String(local.getUTCHours()).padStart(2, "0")}:00`;
    const response = await writeCsv(env, serializeCsv(merged), sha, `Пробки Иркутск: ${stamp}`);
    if (response.ok) return { written: true, added, replaced, answered, history: merged };

    // 409 и 422 - кто-то записал файл, пока мы считали: перечитываем и пробуем
    // снова. Остальные коды повторять бессмысленно.
    if (response.status !== 409 && response.status !== 422) {
      throw new Error(`GitHub отдал ${response.status} на запись: ${await response.text()}`);
    }
  }
  throw new Error(`Не удалось записать за ${PUT_ATTEMPTS} попытки - файл всё время меняется`);
}

/** Короткий отчёт о состоянии ряда - для глазами и без записи. */
async function status(env, now = new Date()) {
  if (!env.GITHUB_TOKEN) throw new Error("Нет GITHUB_TOKEN - читать историю нечем");
  const local = toIrkutsk(now);
  const { rows } = await readCsv(env);
  const today = localDate(local);
  const hoursToday = new Set(rows.filter((row) => row.date === today && row.score !== "").map((row) => row.hour));
  const last = rows.length > 0 ? rows[rows.length - 1] : null;
  return {
    now: formatLocal(local),
    window_open: isWindowOpen(local),
    hours_today: hoursToday.size,
    last_measurement: last ? `${last.date} ${last.hour}:00` : null,
    total_rows: rows.length,
    // Только факт настройки, без значений: по нему видно, дошли ли секреты,
    // и не нужно лезть в дашборд. Сами ключи наружу не отдаются никогда.
    telegram_configured: Boolean(env.TELEGRAM_TOKEN && env.TELEGRAM_CHAT_ID),
    problems: inspect(rows, local).problems,
  };
}

export default {
  async scheduled(event, env) {
    const now = new Date();
    const report = await tick(env, now);

    // Сторож смотрит на то же состояние, что получил сбор - лишнего запроса к
    // GitHub не делаем. Ночью (окно закрыто) истории нет и проверять нечего.
    if (report.history) {
      const message = watchMessage(report.history, toIrkutsk(now));
      if (message) {
        // Телеграм - если настроен. Issue - только на тревогу: письмо о том,
        // что всё хорошо, каждый вечер превратилось бы в шум, который перестают
        // читать. Сводку смотреть на дашборде.
        report.telegram = await sendTelegram(env, message.text);
        if (message.kind === "alert") {
          report.issue = await openIssue(env, message.title, message.text);
        }
      }
      delete report.history; // в журнал уходит отчёт, а не весь ряд
    }

    // Уходит в журнал Worker'а. Токенов и секретов тут нет.
    console.log(JSON.stringify(report));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const json = (payload, code = 200) =>
      new Response(`${JSON.stringify(payload, null, 2)}\n`, {
        status: code,
        headers: { "Content-Type": "application/json; charset=utf-8" },
      });

    try {
      // Не настроен секрет - об этом можно сказать прямо: это не утечка, а
      // единственная причина, по которой свежепоставленный воркер молчит.
      if (!env.GITHUB_TOKEN) {
        return json({ error: "воркеру не задан секрет GITHUB_TOKEN" }, 500);
      }
      // Проверка связи с Telegram по требованию. Пускаем того, кто знает
      // chat_id: знание адреса своего же чата - достаточное право отправить
      // туда сообщение, а заспамить чужой чат так нельзя.
      if (url.pathname === "/ping") {
        if (!env.TELEGRAM_TOKEN || !env.TELEGRAM_CHAT_ID) {
          return json({ error: "не заданы TELEGRAM_TOKEN или TELEGRAM_CHAT_ID" }, 500);
        }
        if (url.searchParams.get("chat") !== String(env.TELEGRAM_CHAT_ID)) {
          return json({ error: "chat не совпадает с настроенным" }, 403);
        }
        const result = await sendTelegram(env, "Проверка связи: сторож пробок на месте.");
        return json({ telegram: result });
      }
      if (url.pathname === "/collect") {
        // Ручной прогон закрыт ключом: иначе любой прохожий пишет в репозиторий.
        if (!env.TRIGGER_KEY) return json({ error: "ручной прогон выключен: не задан TRIGGER_KEY" }, 403);
        if (url.searchParams.get("key") !== env.TRIGGER_KEY) return json({ error: "неверный ключ" }, 403);
        const report = await tick(env);
        delete report.history; // наружу отдаём отчёт, а не весь ряд целиком
        return json(report);
      }
      return json(await status(env));
    } catch (error) {
      // Наружу - только факт сбоя: адрес воркера открыт всем, а в ответе
      // GitHub может оказаться лишнее про репозиторий и права токена.
      // Подробности уходят в журнал Cloudflare, где их видит только Анна.
      console.log(`Ошибка обработки запроса: ${error}`);
      return json({ error: "не получилось; подробности в журнале воркера" }, 500);
    }
  },
};

export {
  closedHours,
  collect,
  formatLength,
  formatLocal,
  formatScore,
  formatUtc,
  hoursWord,
  inspect,
  isWindowOpen,
  lastMeasurement,
  parseCsv,
  serializeCsv,
  toIrkutsk,
  upsert,
  watchMessage,
};
