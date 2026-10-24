// Таблица моделей Polytome: статический сайт (GitHub Pages). Данные — index.json (собирает bench/site/build.py),
// переводы подгружаются по клику из файлов результатов. Без библиотек и внешних запросов.
"use strict";

const I18N = {
  ru: {
    title: "Polytome — какая модель хорошо переводит книги",
    meta: (d, n) => `Набор ${d.suite} · ${n} прогонов · обновлено ${d.generated}`,
    intro: [
      "Одна короткая история написана заново на семи языках — китайском, японском, корейском, английском, французском, немецком и испанском — и переведена каждой моделью на русский и английский. Оценивает код, а не другая модель.",
      "<b>Score</b> — среднее трёх долей: абзацы без ошибок сразу после перевода, после починки Polytome и пройденные <b>ловушки со смыслом</b> (идиомы, имена-слова, опущенное подлежащее, числа и даты прописью). Стиль не оценивается; сильнейшие модели проходят все ловушки — тогда различают скорость и токены.",
    ],
    tabMap: "Все пары", tabPair: "Одна пара", howto: "Как добавить свой результат", langBtn: "English",
    model: "Модель", avg: "Среднее", toRu: "на русский", toEn: "на английский", from: "С языка", to: "На язык",
    score: "Score", first: "Сразу", final: "После починки", traps: "Ловушки", failed: "Не прошла",
    sec: "Сек / 1000 зн.", tok: "Токены / 1000 зн.", cost: "$ / 1 млн зн.", runs: "Прогонов", date: "Дата",
    budget: "лимит", overBudget: (b) => `Не уложились в лимит ${b.toLocaleString("ru")} токенов на прогон:`,
    legend: "Цвет — score: ", clickHint: "Нажмите на клетку или строку — откроется сам перевод с ловушками.",
    noRuns: "Для этой пары результатов пока нет.",
    close: "Закрыть", compare: "Сравнить с:", none: "—", source: "Оригинал", whole: "По всему тексту:",
    passed: "пройдена", failedOne: "провал", chapter: (n) => `Глава ${n}`, issues: "Осталось после починки:",
    loadingRun: "Загружаю перевод…", tokens: "токенов", seconds: "с",
    foot: "Результаты присылает сообщество: каждый прогон — отдельный JSON в bench/results. Ключей и адресов серверов в них нет.",
  },
  en: {
    title: "Polytome — which model translates books well",
    meta: (d, n) => `Suite ${d.suite} · ${n} runs · updated ${d.generated}`,
    intro: [
      "One short story is written anew in seven languages — Chinese, Japanese, Korean, English, French, German and Spanish — and every model translates it into Russian and English. Code scores the result, not another model.",
      "<b>Score</b> is the average of three shares: paragraphs clean right after translation, after Polytome's repair, and <b>meaning traps</b> passed (idioms, names that are ordinary words, dropped subjects, numbers and dates in words). Style is not measured; the strongest models pass every trap — then speed and tokens tell them apart.",
    ],
    tabMap: "All pairs", tabPair: "One pair", howto: "Add your result", langBtn: "Русский",
    model: "Model", avg: "Average", toRu: "into Russian", toEn: "into English", from: "From", to: "Into",
    score: "Score", first: "First pass", final: "After repair", traps: "Traps", failed: "Failed",
    sec: "Sec / 1000 ch.", tok: "Tokens / 1000 ch.", cost: "$ / 1M ch.", runs: "Runs", date: "Date",
    budget: "budget", overBudget: (b) => `Did not fit the budget of ${b.toLocaleString("en")} tokens per run:`,
    legend: "Colour is the score: ", clickHint: "Click a cell or a row to see the translation itself with its traps.",
    noRuns: "No results for this pair yet.",
    close: "Close", compare: "Compare with:", none: "—", source: "Original", whole: "Whole text:",
    passed: "passed", failedOne: "failed", chapter: (n) => `Chapter ${n}`, issues: "Left after repair:",
    loadingRun: "Loading the translation…", tokens: "tokens", seconds: "s",
    foot: "Results come from the community: each run is a separate JSON in bench/results, with no keys or server addresses.",
  },
};

const TRAP_EN = {
  spring_and_sister: "“spring” is a season, not the sister's name",
  dropped_subject_1: "dropped subject — the heroine acts (she, not he)",
  dropped_subject_2: "the heroine alone — her gender is kept",
  organization: "organization name from the glossary",
  number_words: "3500 written in words",
  date_words: "18 May written in words",
  name_is_word: "a name that is an ordinary word — not to be translated",
  idiom: "an idiom whose literal translation is wrong",
};
const TRAP_SHORT = {
  ru: { spring_and_sister: "весна", dropped_subject_1: "она 1", dropped_subject_2: "она 2", organization: "организация",
        number_words: "число", date_words: "дата", name_is_word: "имя-слово", idiom: "идиома" },
  en: { spring_and_sister: "spring", dropped_subject_1: "she 1", dropped_subject_2: "she 2", organization: "organization",
        number_words: "number", date_words: "date", name_is_word: "name-word", idiom: "idiom" },
};
const ISSUE = {
  ru: { untranslated: "остатки оригинала", punct: "китайская пунктуация", name_variant: "ошибка в имени",
        unknown_name: "лишнее имя", name_missing: "пропало имя", gender: "ошибка рода", number: "потеряно число",
        number_words: "числа словами", short: "слишком коротко", long: "слишком длинно", missing: "пропуск абзаца",
        duplicate: "повтор", term: "термин" },
  en: { untranslated: "leftovers", punct: "Chinese punctuation", name_variant: "misspelled name", unknown_name: "extra name",
        name_missing: "lost name", gender: "wrong gender", number: "lost number", number_words: "numbers in words",
        short: "too short", long: "too long", missing: "missing paragraph", duplicate: "repeat", term: "term" },
};

let data = null;
let lang = localStorage.getItem("polytome-lang") || ((navigator.language || "en").startsWith("ru") ? "ru" : "en");
let state = { tab: "map", src: "zh", tgt: "ru", sort: { key: "score", dir: -1 } };
const t = () => I18N[lang];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const shortModel = (m) => m.replace(/^[^:]+:/, "");
const langName = (c) => (lang === "ru" ? data.langs[c].ru : data.langs[c].en);
const pairName = (p) => p.split("-").map(langName).join(" → ");
const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toLocaleString(lang, { maximumFractionDigits: d, minimumFractionDigits: d }));

function mean(xs) {
  const v = xs.filter((x) => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

function median(xs) {
  const v = xs.filter((x) => x != null).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// агрегат по модели и паре: медиана прогонов (как в LEADERBOARD.md); «лимит» — если все прогоны пары упёрлись в него
function aggregate() {
  const by = new Map();
  for (const r of data.runs) {
    const k = r.model + "|" + r.pair;
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(r);
  }
  const out = new Map();
  for (const [k, rs] of by) {
    const ok = rs.filter((r) => !r.budget);
    const last = rs.slice().sort((a, b) => (a.date < b.date ? 1 : -1))[0];
    if (!ok.length) { out.set(k, { budget: true, runs: rs.length, last }); continue; }
    const m = (f) => median(ok.map((r) => r[f]));
    const latest = ok.slice().sort((a, b) => (a.date < b.date ? 1 : -1))[0];
    out.set(k, { score: m("score"), first: m("first"), final: m("final"), traps: m("traps"), sec1k: m("sec1k"),
      tok1k: m("tok1k"), cost1m: m("cost1m"), runs: ok.length, failed: latest.failed, last: latest });
  }
  return out;
}

function color(score) {
  if (score == null) return "transparent";
  const x = Math.max(0, Math.min(1, (score - 80) / 20));  // вся борьба — между 80 и 100
  const hue = 8 + x * 120;
  return `hsl(${hue} 65% 55% / ${0.18 + 0.27 * x})`;
}

// ------------------------------------------------------------------ вкладка «Все пары»
function renderMap() {
  const agg = aggregate();
  const models = [...new Set(data.runs.map((r) => r.model))];
  const pairs = data.pairs;
  // среднее по парам (не медиана: иначе 94 на трёх парах из тринадцати прячется за «100»)
  const avg = (m) => mean(pairs.map((p) => agg.get(m + "|" + p)?.score));
  const meanTok = (m) => mean(pairs.map((p) => agg.get(m + "|" + p)?.tok1k));
  models.sort((a, b) => (avg(b) ?? -1) - (avg(a) ?? -1) || (meanTok(a) ?? 1e9) - (meanTok(b) ?? 1e9));
  const groups = data.pairs.reduce((acc, p) => { const g = p.split("-")[1]; acc[g] = (acc[g] || 0) + 1; return acc; }, {});
  let h = `<div class="scroll"><table class="map"><thead><tr><th class="model" rowspan="2">${t().model}</th>`;
  h += `<th rowspan="2">${t().avg}</th>`;
  for (const g of Object.keys(groups)) h += `<th class="group" colspan="${groups[g]}">${g === "ru" ? t().toRu : t().toEn}</th>`;
  h += `</tr><tr>`;
  for (const p of pairs) h += `<th title="${esc(pairName(p))}">${esc(data.langs[p.split("-")[0]].native)}</th>`;
  h += `</tr></thead><tbody>`;
  for (const m of models) {
    const a = avg(m);
    h += `<tr><td class="model"><code>${esc(shortModel(m))}</code></td><td class="avg" style="background:${color(a)}">${fmt(a, 1)}</td>`;
    for (const p of pairs) {
      const c = agg.get(m + "|" + p);
      if (!c) h += `<td class="cell na">—</td>`;
      else if (c.budget) h += `<td class="cell" data-file="${esc(c.last.file)}" title="${esc(pairName(p))}">${t().budget}</td>`;
      else h += `<td class="cell" data-file="${esc(c.last.file)}" style="background:${color(c.score)}" title="${esc(pairName(p))}: ${fmt(c.score, 1)}">${fmt(c.score, 0)}</td>`;
    }
    h += `</tr>`;
  }
  h += `</tbody></table></div>`;
  h += `<div class="legend">${t().legend}${[100, 97, 94, 90, 85, 80].map((s) => `<span><span class="sw" style="background:${color(s)}"></span>${s}</span>`).join("")}</div>`;
  h += `<p class="note">${t().clickHint}</p>`;
  $("tab-map").innerHTML = h;
  $("tab-map").querySelectorAll("td[data-file]").forEach((td) => td.addEventListener("click", () => openRun(td.dataset.file)));
}

// ------------------------------------------------------------------ вкладка «Одна пара»
function renderPair() {
  const sources = [...new Set(data.pairs.map((p) => p.split("-")[0]))];
  const targets = [...new Set(data.pairs.map((p) => p.split("-")[1]))];
  if (state.src === state.tgt) state.src = sources.find((s) => s !== state.tgt);
  const pair = `${state.src}-${state.tgt}`;
  let h = `<div class="pickers"><div class="group"><span class="label">${t().from}</span>`;
  for (const s of sources) {
    const disabled = !data.pairs.includes(`${s}-${state.tgt}`);
    h += `<button class="chip" data-src="${s}" aria-pressed="${s === state.src}" ${disabled ? "disabled" : ""}>${esc(langName(s))}</button>`;
  }
  h += `</div><div class="group"><span class="label">${t().to}</span>`;
  for (const g of targets) h += `<button class="chip" data-tgt="${g}" aria-pressed="${g === state.tgt}">${esc(langName(g))}</button>`;
  h += `</div></div>`;

  const agg = aggregate();
  const rows = [...new Set(data.runs.filter((r) => r.pair === pair).map((r) => r.model))]
    .map((m) => ({ model: m, ...agg.get(m + "|" + pair) }));
  const ok = rows.filter((r) => !r.budget);
  const over = rows.filter((r) => r.budget);
  const k = state.sort.key, dir = state.sort.dir;
  ok.sort((a, b) => {
    const va = k === "model" ? shortModel(a.model) : a[k], vb = k === "model" ? shortModel(b.model) : b[k];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (va < vb ? -1 : va > vb ? 1 : 0) * dir || (a.tok1k ?? 1e9) - (b.tok1k ?? 1e9);
  });
  if (!rows.length) {
    $("tab-pair").innerHTML = h + `<p class="note">${t().noRuns}</p>`;
    bindPickers();
    return;
  }
  const cols = [["model", t().model], ["score", t().score], ["first", t().first], ["final", t().final], ["traps", t().traps],
    ["failed", t().failed], ["sec1k", t().sec], ["tok1k", t().tok], ["cost1m", t().cost]];
  h += `<div class="scroll"><table class="pairtable"><thead><tr>`;
  for (const [key, label] of cols) {
    const sortable = key !== "failed";
    const arrow = key === k ? `<span class="arrow">${dir > 0 ? " ▲" : " ▼"}</span>` : "";
    h += `<th class="${key === "model" ? "model " : ""}${sortable ? "sortable" : ""}" ${sortable ? `data-sort="${key}"` : ""}>${label}${arrow}</th>`;
  }
  h += `</tr></thead><tbody>`;
  for (const r of ok) {
    h += `<tr data-file="${esc(r.last.file)}"><td class="model"><code>${esc(shortModel(r.model))}</code></td>`;
    h += `<td style="background:${color(r.score)}"><b>${fmt(r.score, 1)}</b></td>`;
    h += `<td>${fmt(r.first)}%</td><td>${fmt(r.final)}%</td><td>${fmt(r.traps)}%</td>`;
    h += `<td style="text-align:left">${(r.failed || []).map((id) => `<span class="badge bad">${esc(TRAP_SHORT[lang][id] || id)}</span>`).join("")}</td>`;
    h += `<td>${fmt(r.sec1k, 1)}</td><td>${fmt(r.tok1k)}</td><td>${r.cost1m == null ? "—" : "$" + fmt(r.cost1m, 2)}</td></tr>`;
  }
  h += `</tbody></table></div>`;
  if (over.length) h += `<p class="note">${t().overBudget(data.budget)} ${over.map((r) => `<code>${esc(shortModel(r.model))}</code>`).join(", ")}</p>`;
  h += `<p class="note">${t().clickHint}</p>`;
  $("tab-pair").innerHTML = h;
  bindPickers();
  $("tab-pair").querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const key = th.dataset.sort;
    state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : (key === "model" || key === "sec1k" || key === "tok1k" || key === "cost1m" ? 1 : -1) };
    renderPair();
  }));
  $("tab-pair").querySelectorAll("tr[data-file]").forEach((tr) => tr.addEventListener("click", () => openRun(tr.dataset.file)));
}

function bindPickers() {
  $("tab-pair").querySelectorAll("[data-src]").forEach((b) => b.addEventListener("click", () => { state.src = b.dataset.src; saveHash(); renderPair(); }));
  $("tab-pair").querySelectorAll("[data-tgt]").forEach((b) => b.addEventListener("click", () => {
    state.tgt = b.dataset.tgt;
    if (!data.pairs.includes(`${state.src}-${state.tgt}`)) state.src = data.pairs.find((p) => p.endsWith("-" + state.tgt)).split("-")[0];
    saveHash(); renderPair();
  }));
}

// ------------------------------------------------------------------ окно прогона: перевод абзац к абзацу
const cache = new Map();
async function loadRun(file) {
  if (!cache.has(file)) cache.set(file, fetch(file).then((r) => r.json()));
  return cache.get(file);
}

async function openRun(file, cmpFile) {
  const dlg = $("run");
  dlg.innerHTML = `<div class="dlg-head"><h2 id="run-title">${t().loadingRun}</h2></div>`;
  if (!dlg.open) dlg.showModal();
  const run = await loadRun(file);
  const cmp = cmpFile ? await loadRun(cmpFile) : null;
  const [src, tgt] = run.pair.split("-");
  const traps = data.traps[run.pair];
  const failed = new Set((run.traps_failed || []).map((x) => x.id));
  const failedCmp = new Set(((cmp && cmp.traps_failed) || []).map((x) => x.id));
  const why = (tr) => (lang === "ru" ? tr.why : TRAP_EN[tr.id] || tr.why);
  const others = data.runs.filter((r) => r.pair === run.pair && r.file !== file && !r.budget)
    .sort((a, b) => shortModel(a.model).localeCompare(shortModel(b.model)));
  const issues = Object.entries(run.issues_final || {}).map(([k, v]) => `${ISSUE[lang][k] || k} ×${v}`).join(", ");

  let h = `<div class="dlg-head"><button class="chip close" type="button" id="dlg-close">${t().close}</button>`;
  h += `<h2 id="run-title"><code>${esc(shortModel(run.model))}</code> · ${esc(pairName(run.pair))}</h2>`;
  h += `<div class="metrics"><span>${t().score} <b>${fmt(run.score, 1)}</b></span><span>${t().first} <b>${fmt(run.first_pass_ok)}%</b></span>`;
  h += `<span>${t().final} <b>${fmt(run.final_ok)}%</b></span><span>${t().traps} <b>${fmt(run.traps_ok)}%</b></span>`;
  h += `<span><b>${fmt((run.tokens?.prompt || 0) + (run.tokens?.completion || 0))}</b> ${t().tokens}</span><span><b>${fmt(run.seconds, 0)}</b> ${t().seconds}</span>`;
  h += `<span>${esc(run.date)} · Polytome ${esc(run.polytome)}</span></div>`;
  h += `<div class="traps">${traps.map((tr) => `<span class="badge ${failed.has(tr.id) ? "bad" : "ok"}" title="${esc(why(tr))}">${failed.has(tr.id) ? "✗" : "✓"} ${esc(TRAP_SHORT[lang][tr.id] || tr.id)}</span>`).join("")}</div>`;
  if (issues) h += `<div class="metrics">${t().issues} ${esc(issues)}</div>`;
  h += `<div class="compare"><label for="cmp">${t().compare}</label><select id="cmp"><option value="">${t().none}</option>`;
  for (const o of others) h += `<option value="${esc(o.file)}" ${o.file === cmpFile ? "selected" : ""}>${esc(shortModel(o.model))} (${fmt(o.score, 1)})</option>`;
  h += `</select></div></div><div class="dlg-body">`;

  const whole = traps.filter((tr) => tr.ch == null);
  if (whole.length) {
    h += `<p class="note">${t().whole} ${whole.map((tr) => `<span class="trapnote ${failed.has(tr.id) ? "bad" : "ok"}">${failed.has(tr.id) ? "✗" : "✓"} ${esc(why(tr))}</span>`).join(" ")}</p>`;
  }
  const colsN = cmp ? 3 : 2;
  const grid = `grid-template-columns: repeat(${colsN}, minmax(0, 1fr))`;
  data.texts[src].forEach((paras, ci) => {
    const chap = String(ci + 1);
    h += `<div class="chapter"><h3>${t().chapter(ci + 1)}</h3><div class="paras">`;
    h += `<div class="prow head" style="${grid}"><div>${t().source} · ${esc(langName(src))}</div><div>${esc(shortModel(run.model))}</div>${cmp ? `<div>${esc(shortModel(cmp.model))}</div>` : ""}</div>`;
    paras.forEach((p, pi) => {
      const tr = traps.find((x) => x.ch === ci + 1 && x.p === pi);
      const note = (set) => (tr ? `<span class="trapnote ${set.has(tr.id) ? "bad" : "ok"}">${set.has(tr.id) ? "✗ " + t().failedOne : "✓ " + t().passed}: ${esc(why(tr))}</span>` : "");
      h += `<div class="prow ${pi === 0 ? "title" : ""} ${tr ? "trap" : ""}" style="${grid}">`;
      h += `<div class="src">${esc(p)}</div>`;
      h += `<div>${esc(run.translation?.[chap]?.[pi] ?? "")}${note(failed)}</div>`;
      if (cmp) h += `<div>${esc(cmp.translation?.[chap]?.[pi] ?? "")}${note(failedCmp)}</div>`;
      h += `</div>`;
    });
    h += `</div></div>`;
  });
  h += `</div>`;
  dlg.innerHTML = h;
  $("dlg-close").addEventListener("click", () => dlg.close());
  $("cmp").addEventListener("change", (e) => openRun(file, e.target.value || null));
  writeHash("run=" + encodeURIComponent(file) + (cmpFile ? "&cmp=" + encodeURIComponent(cmpFile) : ""));
}

// ------------------------------------------------------------------ общее
// адрес страницы повторяет вкладку, пару и открытый прогон: ссылкой можно поделиться, «Назад» возвращает
let ownHash = null; // хэш, который записала сама страница: его hashchange — не переход
function writeHash(h) {
  ownHash = h ? "#" + h : "";
  location.hash = h;
}

function saveHash() {
  writeHash(state.tab === "pair" ? `pair=${state.src}-${state.tgt}` : "");
}

// «Назад», «Вперёд» или ссылка с другим хэшем на уже открытой странице
function onHashChange() {
  if (location.hash === ownHash) return;
  ownHash = null;
  const q = readHash();
  if (q.get("run")) return openRun(q.get("run"), q.get("cmp"));
  if (!q.get("pair")) state.tab = "map";
  if ($("run").open) $("run").close();
  renderPair();
  setTab(state.tab);
}

function readHash() {
  const q = new URLSearchParams(location.hash.slice(1));
  if (q.get("pair") && data.pairs.includes(q.get("pair"))) {
    [state.src, state.tgt] = q.get("pair").split("-");
    state.tab = "pair";
  }
  return q;
}

function setTab(tab) {
  state.tab = tab;
  for (const name of ["map", "pair"]) {
    $("tab-" + name).hidden = name !== tab;
    $(`tab-${name}-btn`).setAttribute("aria-selected", String(name === tab));
  }
  saveHash();
}

function renderAll() {
  document.documentElement.lang = lang;
  document.title = t().title;
  $("title").textContent = t().title;
  $("meta").textContent = t().meta(data, data.runs.length);
  $("intro").innerHTML = t().intro.map((p) => `<p>${p}</p>`).join("");
  $("tab-map-btn").textContent = t().tabMap;
  $("tab-pair-btn").textContent = t().tabPair;
  $("howto").textContent = t().howto;
  $("lang-btn").textContent = t().langBtn;
  $("foot").textContent = t().foot;
  renderMap();
  renderPair();
  setTab(state.tab);
}

async function main() {
  data = await fetch("index.json").then((r) => r.json());
  $("loading").remove();
  const q = readHash();
  renderAll();
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $("lang-btn").addEventListener("click", () => {
    lang = lang === "ru" ? "en" : "ru";
    localStorage.setItem("polytome-lang", lang);
    renderAll();
  });
  $("run").addEventListener("close", () => saveHash());
  if (q.get("run")) openRun(q.get("run"), q.get("cmp"));
  window.addEventListener("hashchange", onHashChange);
}

main().catch((e) => { $("loading").textContent = "Error: " + e; });
