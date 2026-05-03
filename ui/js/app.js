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
    if (backendDown) { btnStart.disabled = true; btnStop.disabled = true; return; }
    btnStart.disabled = running;
    btnStop.disabled = !running;
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

        const lu = document.getElementById("m-last-update");
        if (lu) lu.textContent = toKstString(new Date().toISOString()) + " KST";

        _setButtons(btnStart, btnStop, s.running, false);
    } catch (_) {
        connDot.style.background = "#fb7185";
        connDot.style.boxShadow  = "0 0 8px #fb7185";
        connLabel.textContent = "DISCONNECTED";
        _setStatusBadge(mStatus, false, true);
        _setButtons(btnStart, btnStop, false, true);
    }
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

async function loadConfigToToggles() {
    try {
        const cfg = await Api.getConfig();
        document.querySelectorAll("[data-config]").forEach((input) => {
            const key = input.dataset.config;
            if (key in cfg) input.checked = !!cfg[key];
        });
    } catch (_) {
        /* 미연결 시 토글은 false 유지 */
    }
}

document.getElementById("btn-save-config")?.addEventListener("click", async () => {
    const msg = document.getElementById("config-msg");
    const cfg = {};
    document.querySelectorAll("[data-config]").forEach((input) => {
        cfg[input.dataset.config] = !!input.checked;
    });
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
        liveConn = Api.connectLiveLog(
            (record) => {
                pushRecord(record);
                setStatus("LIVE", true);
            },
            (reason) => {
                setStatus(`LIVE 끊김: ${reason}`, false);
                stopLive();
                // 자동 재연결: 토글이 여전히 켜져있을 때만 5초 후 재시도
                if ($autoStream()?.checked) {
                    setTimeout(() => { if ($autoStream()?.checked) startLive(); }, 5000);
                }
            }
        );
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
            buffer.length = 0;
            Array.from(box.querySelectorAll(".log-line")).forEach((el) => el.remove());
            const empty = $empty();
            if (empty) empty.style.display = "block";
            updateCount();
            setStatus("화면 비움 (버퍼 유지)", true);
        });
        $("log-download")?.addEventListener("click", () => {
            // 다운로드는 buffer 전체 (필터 무시) — 운영 분석 시 누락 방지.
            const text = buffer.map((r) =>
                `${toKstString(r.ts)} [${r.level}] ${r.logger || ""}: ${r.message}`
            ).join("\n");
            const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `aurora_logs_${Date.now()}.txt`;
            a.click();
            URL.revokeObjectURL(url);
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
