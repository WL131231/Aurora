// Aurora GUI 진입점 — 라우팅 + 데이터 바인딩 + UI 인터랙션.
//
// vanilla JS — Pywebview 환경에서 가벼움 우선.
//
// 담당: 정용우

const Api = window.AuroraApi;

// ============================================================
// 0. 부팅 스플래시 — AURORA 페이드 인 → 정지 → 대시보드
// ============================================================
//
// 타이밍 (CSS 의 splash-fade-in 1.5s 와 동기):
//   0.0s        splash 페이드 인 시작
//   1.5s        페이드 인 끝 (글자 완전 표시 + 그라디언트 시프트 시작)
//   2.5s        오버레이 fade-out 클래스 추가 (0.8s 페이드 아웃)
//   3.3s        오버레이 DOM 제거 + body splash-active 해제 → 메인 GUI 인터랙션
//
// 이 사이 메인 셸 (.main-shell) 은 opacity 0 (CSS) → 페이드 인 0.6s @ delay 0.4s
// 로 자연스럽게 등장.

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
    }, 2500); // 1.5s (페이드 인) + 1.0s (정지)
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

async function refreshDashboard() {
    const connDot = document.getElementById("conn-dot");
    const connLabel = document.getElementById("conn-label");
    const modeLabel = document.getElementById("mode-label");

    try {
        const s = await Api.status();
        connDot.style.background = "#22d3ee";
        connDot.style.boxShadow = "0 0 8px #22d3ee";
        connLabel.textContent = "CONNECTED";

        modeLabel.textContent = (s.mode || "—").toUpperCase();
        document.getElementById("m-mode").textContent = (s.mode || "—").toUpperCase();
        document.getElementById("m-status").textContent = s.running ? "실행 중" : "중지";
        document.getElementById("m-status").style.color = s.running
            ? "#22d3ee"
            : "rgba(255,255,255,0.7)";
        document.getElementById("m-positions").textContent = String(s.open_positions ?? 0);
        document.getElementById("m-equity").textContent =
            s.equity_usd === null || s.equity_usd === undefined
                ? "—"
                : `$ ${s.equity_usd.toFixed(2)}`;
    } catch (e) {
        connDot.style.background = "#fb7185";
        connDot.style.boxShadow = "0 0 8px #fb7185";
        connLabel.textContent = "DISCONNECTED";
        document.getElementById("m-status").textContent = "API 미연결";
        document.getElementById("m-status").style.color = "#fb7185";
    }
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
    try {
        const r = await Api.startBot();
        alert(r.success ? "봇 시작됨" : `시작 실패: ${r.message}`);
        refreshDashboard();
    } catch (e) {
        alert(`API 오류: ${e.message}`);
    }
});

document.getElementById("btn-stop")?.addEventListener("click", async () => {
    try {
        const r = await Api.stopBot();
        alert(r.success ? "봇 중지됨" : `중지 실패: ${r.message}`);
        refreshDashboard();
    } catch (e) {
        alert(`API 오류: ${e.message}`);
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
// 11. 초기 로드 + 폴링
// ============================================================

refreshDashboard();
loadConfigToToggles();

// 5초 주기 대시보드 폴링 (TODO 정용우: WebSocket /ws/live 로 전환)
setInterval(refreshDashboard, 5000);
