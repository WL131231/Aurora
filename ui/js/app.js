// Aurora GUI 진입점 — 라우팅 + 데이터 바인딩.
//
// 단일 ``index.html`` 안의 6개 ``[data-view-content]`` 섹션을 JS로 토글.
// 외부 라이브러리 없이 vanilla JS — Pywebview 환경에서 가벼움 우선.
//
// 담당: 정용우

const Api = window.AuroraApi;

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
// 2. 연결 상태 + 대시보드 메트릭 폴링
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
// 3. 전략 / 지표 토글 (use_* 4개)
// ============================================================

async function loadConfigToToggles() {
    try {
        const cfg = await Api.getConfig();
        document.querySelectorAll("[data-config]").forEach((input) => {
            const key = input.dataset.config;
            if (key in cfg) input.checked = !!cfg[key];
        });
    } catch (_) {
        // 미연결 시 무시 — 토글 모두 false 상태로 둠
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
// 4. 봇 시작 / 중지 버튼
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
// 5. 초기 로드 + 폴링
// ============================================================

refreshDashboard();
loadConfigToToggles();

// 5초 주기 대시보드 폴링 (TODO 정용우: WebSocket /ws/live 로 전환)
setInterval(refreshDashboard, 5000);
