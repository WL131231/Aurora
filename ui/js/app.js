// Aurora GUI 진입점 — 라우팅 + 데이터 바인딩 + UI 인터랙션.
//
// vanilla JS — Pywebview 환경에서 가벼움 우선.
//
// 담당: 정용우

const Api = window.AuroraApi;

// ============================================================
// 0. 부팅 스플래시 — AURORA 페이드 인 → 대시보드 (끊김 없이)
// ============================================================
//
// 타이밍 (CSS splash-fade-in 2s 와 동기):
//   0.0s        splash 페이드 인 시작 + 그라디언트 시프트 동시 시작
//   2.0s        페이드 인 끝 (글자 완전 표시, 시프트 계속 진행)
//   3.0s        오버레이 fade-out 클래스 추가 (0.8s 페이드 아웃)
//   3.8s        DOM 제거 + body splash-active 해제 → 메인 GUI 인터랙션
//
// 끊김 방지:
//   - 페이드 인 + 그라디언트 시프트 동시 시작 (delay 없음 → 경계 없음)
//   - opacity 만 변화 (transform/blur 제거)

window.addEventListener("load", () => {
    setTimeout(() => {
        const splash = document.getElementById("splash");
        if (!splash) return;
        splash.classList.add("fade-out");
        // 페이드 아웃 끝(0.8s) 후 DOM 제거 + body 클래스 해제
        setTimeout(() => {
            splash.remove();
            document.body.classList.remove("splash-active");
        }, 800);
    }, 3000); // 2.0s (페이드 인) + 1.0s (정지)
});

// ============================================================
// 1. 라우팅 (사이드바 네비 → view 토글)
// ============================================================

function switchView(viewName) {
    document.querySelectorAll(".nav-btn").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.view === viewName);
    });
    document.querySelectorAll("[data-view-content]").forEach((sec) => {
        sec.classList.toggle("view-active", sec.dataset.viewContent === viewName);
    });
}

document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
});

// ============================================================
// 2. 슬라이더 라이브 업데이트 (값 표시)
// ============================================================

function bindSlider(inputId, valueId, formatter = (v) => v) {
    const input = document.getElementById(inputId);
    const value = document.getElementById(valueId);
    if (!input || !value) return;
    const update = () => {
        value.textContent = formatter(input.value);
    };
    input.addEventListener("input", update);
    update();
}

bindSlider("vol-period", "vol-period-val", (v) => v);
bindSlider("vol-mult", "vol-mult-val", (v) => `${parseFloat(v).toFixed(1)}×`);
bindSlider("vol-boost", "vol-boost-val", (v) => `${parseFloat(v).toFixed(1)}×`);

bindSlider("tp1", "tp1-val", (v) => `${v}%`);
bindSlider("tp2", "tp2-val", (v) => `${v}%`);
bindSlider("tp3", "tp3-val", (v) => `${v}%`);
bindSlider("tp4", "tp4-val", (v) => `${v}%`);

bindSlider("min-seed", "min-seed-val", (v) => `${v}%`);
bindSlider("risk-pct", "risk-pct-val", (v) => `${parseFloat(v).toFixed(1)}%`);

// ============================================================
// 3. 레버리지 슬라이더 → 자동 SL/TP 계산 (risk.py 룰 동등)
// ============================================================

function slPctForLeverage(L) {
    // src/aurora/core/risk.py 의 sl_pct_for_leverage 와 동일 공식.
    if (L <= 37) return 2.0 + (L - 10) / 27.0;
    return 0.08 * L;
}

function tpRangeForLeverage(L) {
    const sl = slPctForLeverage(L);
    if (L <= 37) return [sl + 0.8, sl + 1.8];
    return [sl + 2.0, sl + 3.0];
}

const levInput = document.getElementById("lev");
const levVal = document.getElementById("lev-val");
const autoSlEl = document.getElementById("auto-sl");
const autoTpEl = document.getElementById("auto-tp");

function updateLevDisplay() {
    if (!levInput) return;
    const L = parseInt(levInput.value, 10);
    levVal.textContent = `${L}×`;
    autoSlEl.textContent = `${slPctForLeverage(L).toFixed(2)}%`;
    const [tpMin, tpMax] = tpRangeForLeverage(L);
    autoTpEl.textContent = `${tpMin.toFixed(2)} ~ ${tpMax.toFixed(2)}%`;
}

if (levInput) {
    levInput.addEventListener("input", updateLevDisplay);
    updateLevDisplay();
}

// ============================================================
// 4. TP 4단계 분할 합계 검증 (합 100 권장)
// ============================================================

const tpInputs = ["tp1", "tp2", "tp3", "tp4"].map((id) => document.getElementById(id));
const tpSumEl = document.getElementById("tp-sum");

function updateTpSum() {
    if (!tpSumEl || tpInputs.some((i) => !i)) return;
    const sum = tpInputs.reduce((acc, i) => acc + parseInt(i.value, 10), 0);
    const color = sum === 100 ? "var(--aurora-purple)" : "#fbbf24";
    tpSumEl.innerHTML = `합계: <span style="color: ${color}">${sum}%</span>`;
}

tpInputs.forEach((i) => i?.addEventListener("input", updateTpSum));
updateTpSum();

// ============================================================
// 5. 페어 카드 토글 (선택 / 해제)
// ============================================================

document.querySelectorAll(".pair-card").forEach((card) => {
    card.addEventListener("click", () => {
        card.classList.toggle("selected");
        const meta = card.querySelector(".pair-meta");
        if (meta) {
            meta.textContent = card.classList.contains("selected") ? "SELECTED" : "—";
        }
    });
});

// ============================================================
// 6. 연결 상태 + 대시보드 메트릭 폴링
// ============================================================

// UTC ISO → KST 표시 (Aurora 정책)
function toKstString(isoStr) {
    return new Date(isoStr).toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul", hour12: false,
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
    });
}

function _setStatusBadge(el, running, backendDown) {
    el.className = "status-badge " +
        (backendDown ? "badge-error" : running ? "badge-running" : "badge-stopped");
    el.textContent = backendDown ? "연결 끊김" : running ? "실행 중" : "중지";
}

function _setButtons(btnStart, btnStop, running, backendDown) {
    if (!btnStart || !btnStop) return;
    const btnRestart = document.getElementById("btn-restart");
    if (backendDown) {
        btnStart.disabled = true;
        btnStop.disabled = true;
        if (btnRestart) btnRestart.disabled = true;
        return;
    }
    btnStart.disabled = running;
    btnStop.disabled = !running;
    // 재시작은 백엔드 살아있으면 항상 가능 (stop+start 묶음 — running 상관 X)
    if (btnRestart) btnRestart.disabled = false;
}

async function refreshDashboard() {
    const connDot   = document.getElementById("conn-dot");
    const connLabel = document.getElementById("conn-label");
    const modeLabel = document.getElementById("mode-label");
    const btnStart  = document.getElementById("btn-start");
    const btnStop   = document.getElementById("btn-stop");
    const mStatus   = document.getElementById("m-status");

    try {
        const s = await Api.status();

        connDot.style.background = "#22d3ee";
        connDot.style.boxShadow  = "0 0 8px #22d3ee";
        connLabel.textContent = "CONNECTED";

        const mode = (s.mode || "").toUpperCase();
        modeLabel.textContent = mode;
        document.getElementById("m-mode").textContent = mode;

        _setStatusBadge(mStatus, s.running, false);

        document.getElementById("m-positions").textContent = String(s.open_positions ?? 0);
        document.getElementById("m-equity").textContent =
            s.equity_usd == null ? "—"
                : s.equity_usd.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        // equity 실 값 들어오면 stub 메시지 숨김
        const stub = document.getElementById("m-equity-stub");
        if (stub) stub.style.display = (s.equity_usd == null) ? "" : "none";

        const lu = document.getElementById("m-last-update");
        if (lu) lu.textContent = toKstString(new Date().toISOString()) + " KST";

        _setButtons(btnStart, btnStop, s.running, false);

        // 열린 포지션 표 — /positions 호출 + 행 렌더
        await refreshPositions();
    } catch (_) {
        connDot.style.background = "#fb7185";
        connDot.style.boxShadow  = "0 0 8px #fb7185";
        connLabel.textContent = "DISCONNECTED";
        _setStatusBadge(mStatus, false, true);
        _setButtons(btnStart, btnStop, false, true);
    }
}

// 열린 포지션 표 갱신 — /positions API 호출 + tbody 행 렌더링
async function refreshPositions() {
    const tbody = document.getElementById("pos-tbody");
    if (!tbody) return;
    let positions = [];
    try {
        positions = await Api.getPositions();
    } catch (_) {
        // 백엔드 끊기면 status 분기에서 이미 처리, 여기는 빈 표 유지
        return;
    }
    if (!Array.isArray(positions) || positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="pos-empty">열린 포지션 없음</td></tr>';
        return;
    }
    const fmtPrice = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const fmtPnl = (v) => {
        const n = Number(v);
        const sign = n >= 0 ? "+" : "";
        const color = n >= 0 ? "#34d399" : "#fb7185";
        return `<span style="color:${color}">${sign}${n.toFixed(2)} USDT</span>`;
    };
    tbody.innerHTML = positions.map(p => `
        <tr>
            <td class="mono">${p.symbol}</td>
            <td>${p.direction === "long" ? "롱" : "숏"}</td>
            <td class="mono">${fmtPrice(p.entry_price)}</td>
            <td class="mono">${Number(p.quantity).toFixed(4)}</td>
            <td class="mono">${p.leverage}×</td>
            <td class="mono">${fmtPnl(p.unrealized_pnl_usd)}</td>
        </tr>
    `).join("");
}

// 제어 버튼 인라인 피드백 — success: 3초, error: 5초 + × 닫기
function showCtrlMsg(text, ok) {
    const el = document.getElementById("ctrl-msg");
    if (!el) return;
    clearTimeout(el._t);
    const delay = ok ? 3000 : 5000;
    el.innerHTML =
        `<span style="color:${ok ? "#22d3ee" : "#fb7185"}">${text}</span>` +
        (ok ? "" : ` <span class="ctrl-msg-close" onclick="this.parentElement.innerHTML=''">×</span>`);
    el._t = setTimeout(() => { el.innerHTML = ""; }, delay);
}

// ============================================================
// 7. 전략 / 지표 토글 (use_* 4개)
// ============================================================

// data-config 속성을 가진 입력 type 별 적용/수집 (checkbox / radio / range / number / text/select)
function _applyConfigValue(input, val) {
    if (val === undefined || val === null) return;
    if (input.type === "checkbox") {
        input.checked = !!val;
    } else if (input.type === "radio") {
        // 같은 name 라디오 그룹 — value 매칭되는 것만 checked
        input.checked = (input.value === String(val));
    } else if (input.type === "range" || input.type === "number") {
        input.value = String(val);
        // 슬라이더 표시값 (lev-val 등) 갱신 트리거
        input.dispatchEvent(new Event("input"));
    } else {
        input.value = String(val);
    }
}

function _collectConfigValue(input) {
    if (input.type === "checkbox") return !!input.checked;
    if (input.type === "radio") return input.checked ? input.value : undefined;
    if (input.type === "range" || input.type === "number") return parseFloat(input.value);
    return input.value;
}

async function loadConfigToToggles() {
    try {
        const cfg = await Api.getConfig();
        // 1. data-config 속성 모든 입력 — checkbox / radio / range / number 포괄
        document.querySelectorAll("[data-config]").forEach((input) => {
            const key = input.dataset.config;
            if (key in cfg) _applyConfigValue(input, cfg[key]);
        });
        // 2. 페어 카드 — primary_symbol 와 매칭 (예: "BTC/USDT:USDT" → "BTC/USDT")
        const primary = cfg.primary_symbol;
        if (primary) {
            const pairKey = primary.split(":")[0];  // ":USDT" suffix 제거
            document.querySelectorAll(".pair-card").forEach((card) => {
                const isSelected = card.dataset.pair === pairKey;
                card.classList.toggle("selected", isSelected);
                const meta = card.querySelector(".pair-meta");
                if (meta) meta.textContent = isSelected ? "SELECTED" : "—";
            });
        }
    } catch (_) {
        /* 미연결 시 default 유지 */
    }
}

// ===== 외부 사용자 alias 등록 =====
// (API Key + Secret + Nickname) → config_store.user_aliases 에 저장 + bybit_alias 자동 set
// → 다음 진입부턴 alias input 에 nickname 만 입력하면 됨.
document.getElementById("btn-register-alias")?.addEventListener("click", async () => {
    const msg = document.getElementById("register-msg");
    const apiKey = document.getElementById("reg-api-key")?.value.trim();
    const apiSecret = document.getElementById("reg-api-secret")?.value.trim();
    const nickname = document.getElementById("reg-nickname")?.value.trim();

    // 입력 검증 — 셋 중 하나라도 비어있으면 거부
    if (!apiKey || !apiSecret || !nickname) {
        msg.textContent = "API Key / Secret / Nickname 모두 입력 필요";
        msg.style.color = "#fb7185";
        setTimeout(() => { msg.textContent = ""; }, 3000);
        return;
    }

    try {
        // 현재 config 가져와서 user_aliases 에 추가 + bybit_alias 자동 set
        const current = await Api.getConfig();
        const userAliases = current.user_aliases || {};
        userAliases[nickname] = { api_key: apiKey, api_secret: apiSecret };
        const merged = {
            ...current,
            user_aliases: userAliases,
            bybit_alias: nickname,    // 등록 즉시 활성 alias 로 set
        };
        await Api.updateConfig(merged);

        // 상단 alias input 에 nickname 자동 채움
        const aliasInput = document.getElementById("bybit-alias");
        if (aliasInput) aliasInput.value = nickname;

        // 등록 폼 초기화 — 키/시크릿 노출 시간 최소화
        document.getElementById("reg-api-key").value = "";
        document.getElementById("reg-api-secret").value = "";
        document.getElementById("reg-nickname").value = "";

        msg.textContent = `✓ '${nickname}' 등록 완료 — 이후 alias 입력만으로 매매 OK`;
        msg.style.color = "#22d3ee";
    } catch (e) {
        msg.textContent = `등록 실패: ${e.message}`;
        msg.style.color = "#fb7185";
    }
    setTimeout(() => { msg.textContent = ""; }, 5000);
});

document.getElementById("btn-save-config")?.addEventListener("click", async () => {
    const msg = document.getElementById("config-msg");

    // Why: 기존 config 를 base 로 merge — data-config 없는 필드 (user_aliases 등) 보존.
    // 단순히 cfg = {} 로 시작하면 dict 필드가 빈 default 로 덮어써져 등록 데이터 사라짐.
    let cfg = {};
    try {
        cfg = { ...(await Api.getConfig()) };
    } catch (_) {
        /* 첫 호출 또는 미연결 — 빈 dict 에서 시작 */
    }

    // 1. data-config 속성 모든 입력 수집 (override)
    document.querySelectorAll("[data-config]").forEach((input) => {
        const val = _collectConfigValue(input);
        if (val !== undefined) cfg[input.dataset.config] = val;
    });

    // 2. 페어 카드 — 첫 selected 를 primary_symbol 로 (Phase 1 = 단일 페어 매매)
    const firstSelected = document.querySelector(".pair-card.selected");
    if (firstSelected) {
        // ccxt linear perpetual 표준: "BTC/USDT" → "BTC/USDT:USDT"
        cfg.primary_symbol = `${firstSelected.dataset.pair}:USDT`;
    }

    try {
        await Api.updateConfig(cfg);
        msg.textContent = "✓ 저장됨";
        msg.style.color = "#22d3ee";
    } catch (e) {
        msg.textContent = `저장 실패: ${e.message}`;
        msg.style.color = "#fb7185";
    }
    setTimeout(() => {
        msg.textContent = "";
    }, 3000);
});

// ============================================================
// 8. 봇 시작 / 중지 버튼
// ============================================================

document.getElementById("btn-start")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-start");
    const stop = document.getElementById("btn-stop");
    const orig = btn.textContent;
    btn.disabled = true; stop.disabled = true;
    btn.textContent = "시작 중...";
    try {
        const r = await Api.startBot();
        showCtrlMsg(r.success ? "▶ 봇 시작됨" : `시작 실패: ${r.message}`, r.success);
    } catch (e) {
        showCtrlMsg(`API 오류: ${e.message}`, false);
    } finally {
        btn.textContent = orig;
        refreshDashboard();
    }
});

document.getElementById("btn-stop")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-stop");
    const start = document.getElementById("btn-start");
    const orig = btn.textContent;
    btn.disabled = true; start.disabled = true;
    btn.textContent = "중지 중...";
    try {
        const r = await Api.stopBot();
        showCtrlMsg(r.success ? "■ 봇 중지됨" : `중지 실패: ${r.message}`, r.success);
    } catch (e) {
        showCtrlMsg(`API 오류: ${e.message}`, false);
    } finally {
        btn.textContent = orig;
        refreshDashboard();
    }
});

document.getElementById("btn-restart")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-restart");
    const start = document.getElementById("btn-start");
    const stop = document.getElementById("btn-stop");
    const orig = btn.textContent;
    btn.disabled = true;
    if (start) start.disabled = true;
    if (stop) stop.disabled = true;
    btn.textContent = "재시작 중...";
    try {
        const r = await Api.restartBot();
        showCtrlMsg(r.success ? "↻ 봇 재시작됨" : `재시작 실패: ${r.message}`, r.success);
    } catch (e) {
        showCtrlMsg(`API 오류: ${e.message}`, false);
    } finally {
        btn.textContent = orig;
        refreshDashboard();
    }
});

// ============================================================
// 9. 거래소 연결 테스트 (stub)
// ============================================================

document.getElementById("btn-test-conn")?.addEventListener("click", async () => {
    const msg = document.getElementById("conn-test-msg");
    msg.textContent = "테스트 중...";
    msg.style.color = "var(--aurora-purple)";
    try {
        const h = await Api.health();
        msg.textContent = `✓ API 연결됨 (mode=${h.mode})`;
        msg.style.color = "#4ade80";
    } catch (e) {
        msg.textContent = `✗ ${e.message}`;
        msg.style.color = "#fb7185";
    }
});

// ============================================================
// 10. 백테스트 실행 (stub — 진행바 데모만)
// ============================================================

document.getElementById("btn-run-bt")?.addEventListener("click", () => {
    const bar = document.getElementById("bt-progress");
    const label = document.getElementById("bt-progress-label");
    if (!bar || !label) return;
    bar.style.width = "0%";
    label.textContent = "실행 중... (stub demo)";
    let p = 0;
    const interval = setInterval(() => {
        p += 5;
        bar.style.width = `${p}%`;
        if (p >= 100) {
            clearInterval(interval);
            label.textContent = "완료 (stub) — backtest 엔진 본 구현 후 실 결과 표시";
        }
    }, 100);
});

// ============================================================
// 11. Logs view — WebSocket 실시간 + 필터 / 검색 / 다운로드
// ============================================================

const Logs = (() => {
    // 화면·메모리 모두 보호 차원의 상한. 봇 운영 중 INFO 다량 발생해도 GUI 불안정 방지.
    const MAX_LINES = 1000;

    const buffer = [];          // 수신된 record 누적 (FIFO, MAX 도달 시 shift)
    let liveConn = null;        // connectLiveLog 반환 객체
    let initialized = false;

    const $ = (id) => document.getElementById(id);
    const $box = () => $("log-box");
    const $empty = () => $("log-empty");
    const $count = () => $("log-count");
    const $status = () => $("log-status");
    const $autoStream = () => $("log-autostream");
    const $search = () => $("log-search");

    // 사용자가 켠 레벨 set — Python 표준 레벨 키로 저장 (INFO/WARNING/ERROR).
    function enabledLevels() {
        const set = new Set();
        document.querySelectorAll("[data-log-level]").forEach((el) => {
            if (el.checked) set.add(el.dataset.logLevel);
        });
        return set;
    }

    function levelClass(level) {
        if (level === "ERROR" || level === "CRITICAL") return "log-level-error";
        if (level === "WARNING" || level === "WARN") return "log-level-warn";
        return "log-level-info";
    }

    // 필터·검색 매칭. CRITICAL 은 ERROR 체크박스에 흡수 (별도 토글 없음).
    function isVisible(record, levels, query) {
        const lvl = (record.level === "WARN") ? "WARNING"
                  : (record.level === "CRITICAL") ? "ERROR"
                  : record.level;
        if (!levels.has(lvl)) return false;
        if (query) {
            const hay = `${record.message || ""} ${record.logger || ""}`.toLowerCase();
            if (!hay.includes(query.toLowerCase())) return false;
        }
        return true;
    }

    function makeLineEl(record) {
        const el = document.createElement("div");
        el.className = `log-line ${levelClass(record.level)}`;
        el.dataset.level = record.level;
        const ts = document.createElement("span");
        ts.className = "log-line-ts";
        ts.textContent = toKstString(record.ts);
        const lvl = document.createElement("span");
        lvl.className = "log-line-level";
        lvl.textContent = (record.level || "").padEnd(7).slice(0, 7);
        const msg = document.createElement("span");
        msg.className = "log-line-msg";
        msg.textContent = `${record.logger ? record.logger + ": " : ""}${record.message || ""}`;
        el.append(ts, lvl, msg);
        return el;
    }

    function appendLine(record) {
        const box = $box();
        if (!box) return;
        const empty = $empty();
        if (empty) empty.style.display = "none";
        const levels = enabledLevels();
        const query = ($search()?.value) || "";
        // Why: 사용자가 직접 위로 스크롤해서 과거 보고 있으면 강제 자동스크롤 X (UX).
        const wasAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
        const el = makeLineEl(record);
        if (!isVisible(record, levels, query)) el.style.display = "none";
        box.appendChild(el);
        // DOM 라인 수 상한 — buffer 와 별개로 매우 오래된 노드는 떼어냄 (메모리 보호)
        while (box.querySelectorAll(".log-line").length > MAX_LINES) {
            box.querySelector(".log-line")?.remove();
        }
        if (wasAtBottom) box.scrollTop = box.scrollHeight;
    }

    function rerenderAll() {
        const box = $box();
        if (!box) return;
        Array.from(box.querySelectorAll(".log-line")).forEach((el) => el.remove());
        const levels = enabledLevels();
        const query = ($search()?.value) || "";
        let visibleCount = 0;
        for (const r of buffer) {
            const el = makeLineEl(r);
            if (!isVisible(r, levels, query)) el.style.display = "none";
            else visibleCount++;
            box.appendChild(el);
        }
        const empty = $empty();
        if (empty) empty.style.display = visibleCount === 0 ? "block" : "none";
        box.scrollTop = box.scrollHeight;
        updateCount();
    }

    function updateCount() {
        const c = $count();
        if (c) c.textContent = `(${buffer.length} 줄)`;
    }

    function setStatus(text, ok) {
        const el = $status();
        if (!el) return;
        el.textContent = text;
        el.style.color = ok ? "#22d3ee" : "#fb7185";
    }

    function pushRecord(record) {
        buffer.push(record);
        if (buffer.length > MAX_LINES) buffer.shift();
        appendLine(record);
        updateCount();
    }

    async function pollOnce(limit = 100) {
        try {
            const data = await Api.getLogs(limit);
            const lines = (data && data.lines) || [];
            // ts+message 키로 dedup (서버 폴링 결과가 buffer 와 겹칠 수 있음)
            const seen = new Set(buffer.map((r) => `${r.ts}|${r.message}`));
            for (const r of lines) {
                const k = `${r.ts}|${r.message}`;
                if (!seen.has(k)) {
                    buffer.push(r);
                    seen.add(k);
                }
            }
            while (buffer.length > MAX_LINES) buffer.shift();
            rerenderAll();
            setStatus(`폴링 완료 (${lines.length}줄)`, true);
        } catch (e) {
            setStatus(`폴링 실패: ${e.message}`, false);
        }
    }

    function startLive() {
        if (liveConn) return;
        setStatus("실시간 연결 중...", true);
        liveConn = Api.connectLiveLog({
            // open: 연결 확립 시점. 첫 record 안 와도 시각 피드백 제공 (UX).
            onOpen: () => setStatus("LIVE (대기)", true),
            onMessage: (record) => {
                pushRecord(record);
                setStatus("LIVE", true);
            },
            onError: (reason) => {
                setStatus(`LIVE 끊김: ${reason}`, false);
                stopLive();
                // 자동 재연결: 토글이 여전히 켜져있을 때만 5초 후 재시도
                if ($autoStream()?.checked) {
                    setTimeout(() => { if ($autoStream()?.checked) startLive(); }, 5000);
                }
            },
        });
    }

    function stopLive() {
        if (liveConn) {
            liveConn.close();
            liveConn = null;
        }
    }

    function init() {
        if (initialized) return;
        const box = $box();
        if (!box) return;  // Logs view 마크업 없으면 noop
        initialized = true;

        // 필터 체크박스
        document.querySelectorAll("[data-log-level]").forEach((cb) => {
            cb.addEventListener("change", rerenderAll);
        });
        // 검색
        $search()?.addEventListener("input", rerenderAll);
        // 실시간 토글
        $autoStream()?.addEventListener("change", () => {
            if ($autoStream().checked) startLive();
            else stopLive();
        });
        // 버튼들
        $("log-refresh")?.addEventListener("click", () => pollOnce(200));
        $("log-clear")?.addEventListener("click", () => {
            // Why: 화면(DOM) 만 비우고 buffer 는 유지. 새로고침 시 복원 가능 + 새 record 계속 push.
            Array.from(box.querySelectorAll(".log-line")).forEach((el) => el.remove());
            const empty = $empty();
            if (empty) empty.style.display = "block";
            setStatus(`화면 비움 (버퍼 ${buffer.length}줄 유지)`, true);
        });

        // 초기 catch-up: /logs 폴링으로 최근 100 줄 가져오고, 자동 토글 켜져있으면 LIVE 시작.
        pollOnce(100).then(() => {
            if ($autoStream()?.checked) startLive();
        });
    }

    return { init };
})();

Logs.init();

// ============================================================
// 12. 초기 로드 + 폴링
// ============================================================

refreshDashboard();
loadConfigToToggles();

// 15초 주기 대시보드 폴링 (봇 상태 자주 안 바뀜, /start /stop 직후엔 즉시 fetch)
setInterval(refreshDashboard, 15000);
