const SEOUL_CITY_HALL = [37.5665, 126.978];
const SESSION_KEY = "parking-session";
const STATIC = !!window.PARKING_STATIC; // GitHub Pages 버전: 서버 없이 lots.json + 브라우저 계산
const $ = (id) => document.getElementById(id);
const won = (n) => Number(n).toLocaleString("ko-KR");

let lots = [];
let markers = new Map();
let selected = null;
let meterTimer = null;

function storage(op, value) {
  try {
    if (op === "get") return JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    if (op === "set") localStorage.setItem(SESSION_KEY, JSON.stringify(value));
    if (op === "del") localStorage.removeItem(SESSION_KEY);
  } catch { return null; }
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

// 한국 시간 ISO 문자열 ("2026-10-07T16:40:00+09:00")
const KST_MS = 9 * 3600 * 1000;
const toKstIso = (ms) => new Date(ms + KST_MS).toISOString().slice(0, 19) + "+09:00";
const fromLocalInput = (v) => Date.parse(`${v.length === 16 ? v + ":00" : v}+09:00`);

// ---------- 서버 버전 / 정적 버전 ----------
// 주차 기록은 두 버전 모두 이 브라우저에 { id 또는 기록 전체 }로 저장한다.
const serverBackend = {
  async load() {
    const [status, data] = await Promise.all([api("/api/status"), api("/api/lots")]);
    return { status, lots: data };
  },
  async estimate(lot, minutes) {
    return (await api(`/api/lots/${encodeURIComponent(lot.id)}/estimate?minutes=${minutes}`)).fee;
  },
  async start(lot) {
    const s = await api("/api/sessions", { method: "POST", body: JSON.stringify({ lot_id: lot.id }) });
    storage("set", { id: s.id });
    return s;
  },
  async current() {
    const saved = storage("get");
    return saved && saved.id ? api(`/api/sessions/${saved.id}`) : null;
  },
  async changeStart(value) {
    return api(`/api/sessions/${storage("get").id}/start-time`, {
      method: "POST", body: JSON.stringify({ at: value }),
    });
  },
  async end() {
    return api(`/api/sessions/${storage("get").id}/end`, { method: "POST", body: "{}" });
  },
};

function feeText(f) {
  if (f.is_free) return "무료";
  if (!Fee.known(f)) return "요금 정보 없음";
  let t = `기본 ${f.basic_min}분 ${won(f.basic_fee)}원`;
  if (f.add_min) t += `, 추가 ${f.add_min}분당 ${won(f.add_fee)}원`;
  if (f.day_max) t += `, 1일 최대 ${won(f.day_max)}원`;
  return t;
}

function localSession(rec) {
  const until = rec.ended_ms ?? Date.now();
  const minutes = Fee.elapsedMinutes(rec.started_ms, until);
  const p = rec.policy;
  const next = rec.ended_ms ? null : Fee.nextIncreaseMinute(p, minutes);
  return {
    lot_id: rec.lot_id,
    lot_name: rec.lot_name,
    started_at: toKstIso(rec.started_ms),
    ended_at: rec.ended_ms ? toKstIso(rec.ended_ms) : null,
    elapsed_min: minutes,
    fee: Fee.feeForMinutes(p, minutes),
    final: !!rec.ended_ms,
    next_increase_after_min: next,
    next_fee: next != null ? Fee.feeForMinutes(p, next + 1) : null,
    fee_text: feeText(p),
    fee_known: Fee.known(p),
  };
}

const staticBackend = {
  async load() {
    const data = await api(`lots.json?t=${Date.now()}`);
    return { status: data.status, lots: data.lots };
  },
  async estimate(lot, minutes) {
    return Fee.feeForMinutes(lot.fee, minutes);
  },
  async start(lot) {
    const rec = { lot_id: lot.id, lot_name: lot.name, started_ms: Date.now(), policy: lot.fee };
    storage("set", rec);
    return localSession(rec);
  },
  async current() {
    const rec = storage("get");
    return rec && rec.started_ms ? localSession(rec) : null;
  },
  async changeStart(value) {
    const rec = storage("get");
    const ms = fromLocalInput(value);
    if (!(ms <= Date.now())) throw new Error("미래 시각은 넣을 수 없습니다.");
    rec.started_ms = ms;
    storage("set", rec);
    return localSession(rec);
  },
  async end() {
    const rec = storage("get");
    rec.ended_ms = Date.now();
    storage("set", rec);
    return localSession(rec);
  },
};

const backend = STATIC ? staticBackend : serverBackend;
const parking = () => !!storage("get");

// ---------- 화면 ----------
function minutesText(m) {
  const h = Math.floor(m / 60), r = m % 60;
  return h ? `${h}시간 ${r}분` : `${r}분`;
}

function availInfo(lot) {
  if (!lot.realtime) return { cls: "", text: "실시간 정보 없음" };
  if (lot.available === null) return { cls: "", text: "정보 지연" };
  const ratio = lot.capacity ? lot.available / lot.capacity : 0;
  const cls = lot.available === 0 ? "full" : ratio < 0.1 ? "warn" : "ok";
  return { cls, text: lot.available === 0 ? "만차" : `${won(lot.available)}면 남음` };
}

const map = L.map("map").setView(SEOUL_CITY_HALL, 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap",
}).addTo(map);

const COLORS = { ok: "#16a34a", warn: "#d97706", full: "#dc2626", "": "#9ca3af" };

function drawMarkers() {
  markers.forEach((m) => m.remove());
  markers.clear();
  for (const lot of filtered()) {
    if (lot.lat == null || lot.lng == null) continue;
    const { cls } = availInfo(lot);
    const m = L.circleMarker([lot.lat, lot.lng], {
      radius: lot.realtime ? 9 : 5,
      color: "#fff",
      weight: 1.5,
      fillColor: COLORS[cls],
      fillOpacity: 0.9,
    })
      .bindTooltip(lot.name)
      .on("click", () => select(lot.id))
      .addTo(map);
    markers.set(lot.id, m);
  }
}

function filtered() {
  const q = $("q").value.trim();
  const onlyLive = $("only-live").checked;
  return lots.filter((l) =>
    (!onlyLive || l.available !== null) &&
    (!q || l.name.includes(q) || (l.addr || "").includes(q)));
}

function distance(lot) {
  if (lot.lat == null) return Infinity;
  return map.getCenter().distanceTo([lot.lat, lot.lng]);
}

function drawList() {
  const items = filtered()
    .sort((a, b) => (b.realtime - a.realtime) || (distance(a) - distance(b)))
    .slice(0, 80);
  $("list").innerHTML = "";
  for (const lot of items) {
    const { cls, text } = availInfo(lot);
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="row between"><span class="name"></span><span class="badge ${cls}">${text}</span></div>
      <div class="muted small addr"></div>`;
    li.querySelector(".name").textContent = lot.name;
    li.querySelector(".addr").textContent = `${lot.addr || ""} · ${lot.fee.text}`;
    li.onclick = () => select(lot.id);
    $("list").appendChild(li);
  }
  if (!items.length) $("list").innerHTML = `<li class="muted">조건에 맞는 주차장이 없어요.</li>`;
}

function select(id) {
  selected = lots.find((l) => l.id === id);
  if (!selected) return;
  const lot = selected;
  const { cls, text } = availInfo(lot);
  $("d-name").textContent = lot.name;
  $("d-addr").textContent = lot.addr || "주소 정보 없음";
  $("d-avail").innerHTML = `<span class="badge ${cls}">${text}</span> `;
  if (lot.realtime && lot.capacity) {
    const span = document.createElement("span");
    span.className = "muted small";
    span.textContent = ` 전체 ${won(lot.capacity)}면` +
      (lot.count_updated_at ? ` · ${lot.count_updated_at.slice(5, 16)} 기준` : "");
    $("d-avail").appendChild(span);
  }
  $("d-fee").textContent = lot.fee.text;
  $("d-hours").textContent = lot.hours || "-";
  $("d-phone").textContent = lot.phone || "-";
  $("detail").hidden = false;
  $("d-park").disabled = parking();
  $("d-park").textContent = parking() ? "이미 주차 중이에요" : "여기에 주차 완료";
  updateEstimate();
  if (lot.lat != null) map.panTo([lot.lat, lot.lng]);
  $("panel").scrollTop = 0;
}

async function updateEstimate() {
  if (!selected) return;
  if (!selected.fee.known && !selected.fee.is_free) {
    $("d-est").textContent = "알 수 없음";
    return;
  }
  $("d-est").textContent = `${won(await backend.estimate(selected, Number($("d-min").value)))}원`;
}

function showMeter(s) {
  $("meter").hidden = false;
  $("m-lot").textContent = s.lot_name;
  $("m-fee").textContent = won(s.fee);
  $("m-elapsed").textContent = minutesText(s.elapsed_min);
  $("m-start").textContent = s.started_at.slice(5, 16).replace("T", " ");
  $("m-start-input").value = s.started_at.slice(0, 16);
  $("m-rule").textContent = s.fee_known ? s.fee_text : "요금 정보가 없어 계산할 수 없어요.";
  if (s.next_increase_after_min != null) {
    const left = Math.max(0, s.next_increase_after_min - s.elapsed_min);
    $("m-next").textContent = left === 0
      ? `곧 ${won(s.next_fee)}원이 돼요`
      : `${minutesText(left)} 뒤 ${won(s.next_fee)}원이 돼요`;
  } else {
    $("m-next").textContent = "";
  }
}

function showReceipt(s) {
  clearInterval(meterTimer);
  storage("del");
  $("meter").hidden = true;
  $("receipt").hidden = false;
  $("r-lot").textContent = s.lot_name;
  $("r-fee").textContent = won(s.fee ?? 0);
  $("r-time").textContent =
    `${s.started_at.slice(5, 16).replace("T", " ")} ~ ${s.ended_at.slice(11, 16)} · ${minutesText(s.elapsed_min)}`;
}

async function tick() {
  try {
    const s = await backend.current();
    if (!s) return;
    if (s.final) return showReceipt(s);
    showMeter(s);
  } catch {
    storage("del");
    $("meter").hidden = true;
  }
}

function startMeter() {
  clearInterval(meterTimer);
  tick();
  meterTimer = setInterval(tick, STATIC ? 5000 : 15000);
}

async function startParking() {
  if (!selected) return;
  showMeter(await backend.start(selected));
  $("detail").hidden = true;
  startMeter();
}

async function endParking() {
  if (!parking() || !confirm("출차하고 요금을 확정할까요?")) return;
  showReceipt(await backend.end());
}

async function saveStart() {
  const value = $("m-start-input").value;
  if (!value) return;
  try {
    showMeter(await backend.changeStart(value));
    $("m-edit-box").hidden = true;
  } catch (e) {
    alert(e.message);
  }
}

async function loadLots() {
  const { status, lots: data } = await backend.load();
  lots = data;
  const parts = [`실시간 ${status.realtime_lots}곳`, `전체 ${lots.length}곳`];
  if (status.realtime_fetched_at) parts.push(`${status.realtime_fetched_at.slice(5, 16).replace("T", " ")} 갱신`);
  if (status.sample_key) parts.push("⚠ sample 키(5건)로 동작 중");
  if (!status.standard_loaded_at) parts.push("⚠ 표준데이터 미적재");
  $("status").textContent = parts.join(" · ");
  drawMarkers();
  drawList();
}

$("q").addEventListener("input", () => { drawMarkers(); drawList(); });
$("only-live").addEventListener("change", () => { drawMarkers(); drawList(); });
map.on("moveend", drawList);
$("d-close").onclick = () => { $("detail").hidden = true; selected = null; };
$("d-min").onchange = updateEstimate;
$("d-park").onclick = () => startParking().catch((e) => alert(e.message));
$("m-end").onclick = () => endParking().catch((e) => alert(e.message));
$("m-edit").onclick = () => { $("m-edit-box").hidden = !$("m-edit-box").hidden; };
$("m-start-save").onclick = saveStart;
$("r-close").onclick = () => { $("receipt").hidden = true; if (selected) select(selected.id); };

loadLots().catch((e) => { $("status").textContent = `불러오기 실패: ${e.message}`; });
setInterval(() => loadLots().catch(() => {}), 60000);
if (parking()) startMeter();
