// Aurora API 클라이언트 — FastAPI 백엔드 호출 헬퍼.
//
// Pywebview 환경에서 로컬 ``http://127.0.0.1:8765`` 에 띄운 FastAPI 호출.
// 모든 메서드는 Promise 반환. 네트워크 오류는 ``ApiError`` 로 래핑.
//
// 담당: 정용우

const API_BASE = "http://127.0.0.1:8765";

class ApiError extends Error {
    constructor(message, status, payload) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.payload = payload;
    }
}

async function _request(path, options = {}) {
    const url = `${API_BASE}${path}`;
    const init = {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
    };
    let res;
    try {
        res = await fetch(url, init);
    } catch (e) {
        // 네트워크 / CORS / API 미기동 — 호출자에서 retry 또는 표시
        throw new ApiError(`네트워크 오류: ${e.message}`, 0, null);
    }
    let body = null;
    try {
        body = await res.json();
    } catch (_) {
        // JSON 아닌 응답 (text/health 등) — 무시
    }
    if (!res.ok) {
        throw new ApiError(`HTTP ${res.status}`, res.status, body);
    }
    return body;
}

// ─── Health / Status ───────────────────────────────

const health = () => _request("/health");
const status = () => _request("/status");

// ─── Positions ──────────────────────────────────────

const getPositions = () => _request("/positions");

// ─── Trades (거래내역 v0.1.20 + v0.1.23 기간 필터) ──

// days: 0 = bot buffer 만 (거래소 fetch X), 7/30/180 = 거래소 history 도 합침.
// source: "bot" | "exchange" | "all" (기본 all). days>0 + source != bot 이어야 거래소 fetch.
const getTrades = (limit = 200, days = 0, source = "all") =>
    _request(`/trades?limit=${limit}&days=${days}&source=${source}`);

// ─── Stats (결과 통계 v0.1.24) ────────────────────
// days = 거래내역 표 토글과 같은 기간 필터 (7/30/180).
const getStats = (days = 0) => _request(`/stats?days=${days}`);

// ─── Release 알림 (v0.1.25) ────────────────────
const getReleaseLatest = () => _request("/release/latest");

// ─── Market Trend (Coinalyze, v0.1.54) ─────────
const getMarketTrend = () => _request("/market-trend");

// ─── Config ─────────────────────────────────────────

const getConfig = () => _request("/config");
const updateConfig = (config) =>
    _request("/config", { method: "POST", body: JSON.stringify(config) });

// ─── 제어 ──────────────────────────────────────────

const startBot = () => _request("/start", { method: "POST" });
const stopBot = () => _request("/stop", { method: "POST" });
const restartBot = () => _request("/restart", { method: "POST" });

// ─── 로그 ──────────────────────────────────────────

const getLogs = (limit = 100) => _request(`/logs?limit=${limit}`);

// ─── UI 핫 업데이트 (PR b) ────────────────────────

const applyUiUpdate = () => _request("/update/apply_ui", { method: "POST" });

// WebSocket 실시간 로그 — /ws/live 연결 헬퍼.
//   handlers.onOpen():        연결 open 직후 (첫 메시지 도착 전 — UX 피드백용)
//   handlers.onMessage(record): 새 record 수신 (record = {ts, level, logger, message})
//   handlers.onError(reason):   연결 실패 / 끊김 / 에러
//   keepAliveMs:               서버 keep-alive ping 주기 (기본 25초)
//
// 반환: { close() } — 호출 시 깨끗한 종료. 외부에서 토글 끌 때 사용.
//
// 자동 재연결은 호출자가 onError 받고 결정 (예: "실시간" 체크박스 켜져있을 때).
function connectLiveLog(handlers, keepAliveMs = 25000) {
    const { onOpen, onMessage, onError } = handlers || {};
    // ws://127.0.0.1:8765/ws/live (API_BASE 의 http → ws 치환)
    const url = API_BASE.replace(/^http/, "ws") + "/ws/live";
    let ws;
    try {
        ws = new WebSocket(url);
    } catch (e) {
        onError && onError(`WebSocket 생성 실패: ${e.message}`);
        return { close: () => {} };
    }

    let pingTimer = null;
    ws.addEventListener("open", () => {
        // 서버는 receive_text() 로 클라이언트 ping 을 keep-alive 로 받음.
        pingTimer = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, keepAliveMs);
        onOpen && onOpen();
    });
    ws.addEventListener("message", (ev) => {
        try {
            const msg = JSON.parse(ev.data);
            if (msg && msg.type === "log" && msg.data) {
                onMessage(msg.data);
            }
        } catch (_) {
            /* JSON 파싱 실패 무시 (서버 보낸 형식 신뢰) */
        }
    });
    ws.addEventListener("error", () => {
        onError && onError("WebSocket 에러");
    });
    ws.addEventListener("close", () => {
        if (pingTimer) clearInterval(pingTimer);
        onError && onError("WebSocket 연결 종료");
    });

    return {
        close: () => {
            if (pingTimer) clearInterval(pingTimer);
            try { ws.close(); } catch (_) { /* 이미 닫힘 */ }
        },
    };
}

// 글로벌 노출 (app.js 가 사용)
window.AuroraApi = {
    ApiError,
    health,
    status,
    getPositions,
    getTrades,
    getStats,
    getReleaseLatest,
    getMarketTrend,
    getConfig,
    updateConfig,
    startBot,
    stopBot,
    restartBot,
    getLogs,
    connectLiveLog,
    applyUiUpdate,
};
