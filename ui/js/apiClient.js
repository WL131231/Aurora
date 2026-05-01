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

// ─── Config ─────────────────────────────────────────

const getConfig = () => _request("/config");
const updateConfig = (config) =>
    _request("/config", { method: "POST", body: JSON.stringify(config) });

// ─── 제어 ──────────────────────────────────────────

const startBot = () => _request("/start", { method: "POST" });
const stopBot = () => _request("/stop", { method: "POST" });

// ─── 로그 ──────────────────────────────────────────

const getLogs = (limit = 100) => _request(`/logs?limit=${limit}`);

// 글로벌 노출 (app.js 가 사용)
window.AuroraApi = {
    ApiError,
    health,
    status,
    getPositions,
    getConfig,
    updateConfig,
    startBot,
    stopBot,
    getLogs,
};
