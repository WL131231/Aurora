// Aurora Launcher GUI — pywebview js_api 통해 launcher.py 호출.

// pywebview js_api 는 모듈 로드 시점엔 미주입 — pywebviewready 이벤트 후 사용 가능.
// const 로 잡으면 영원히 undefined → 버튼 silent fail. let + ready 에서 갱신.
let Api = null;

const localVerEl = document.getElementById("local-ver");
const latestVerEl = document.getElementById("latest-ver");
const launcherVerEl = document.getElementById("launcher-ver");
const statusEl = document.getElementById("status-msg");
const logList = document.getElementById("log-list");
const btnCheck = document.getElementById("btn-check");
const btnLaunch = document.getElementById("btn-launch");

let pendingUpdateUrl = null;

function log(msg) {
    const li = document.createElement("li");
    li.textContent = msg;
    logList.appendChild(li);
    logList.parentElement.scrollTop = logList.parentElement.scrollHeight;
}

function setStatus(text, color) {
    statusEl.textContent = text;
    statusEl.style.color = color || "var(--text-2)";
}

async function loadLocalInfo() {
    if (!Api) return;
    try {
        const v = await Api.get_local_version();
        localVerEl.textContent = (v && v !== "unknown") ? `v${v}` : "—";
        const lv = await Api.get_launcher_version();
        launcherVerEl.textContent = lv;
    } catch (e) {
        log(`로컬 정보 조회 실패: ${e.message}`);
    }
}

async function checkUpdate() {
    if (!Api) return;
    btnCheck.disabled = true;
    setStatus("업데이트 체크 중...", "var(--aurora-cyan)");
    try {
        const r = await Api.check_update();
        if (r.error) {
            setStatus(`✗ ${r.error}`, "#fb7185");
            log(r.error);
            return;
        }
        latestVerEl.textContent = r.latest || "—";
        if (r.has_update) {
            pendingUpdateUrl = r.url;
            setStatus(`새 버전 ${r.latest} 사용 가능 — 다운로드 + 시작`, "var(--aurora-purple)");
            log(`업데이트 감지: ${r.latest}`);
            btnLaunch.textContent = "⬇ 업데이트 + 시작";
        } else {
            setStatus("최신 버전 사용 중", "#34d399");
            log(`최신 버전 (${r.latest})`);
        }
    } catch (e) {
        setStatus(`✗ ${e.message}`, "#fb7185");
        log(`체크 실패: ${e.message}`);
    } finally {
        btnCheck.disabled = false;
    }
}

async function launchAurora() {
    if (!Api) return;
    btnLaunch.disabled = true;
    btnCheck.disabled = true;

    // 업데이트 보류 중이면 먼저 다운로드 + swap
    if (pendingUpdateUrl) {
        setStatus("업데이트 다운로드 중... (잠시 대기)", "var(--aurora-purple)");
        log("다운로드 시작");
        try {
            const r = await Api.download_and_swap(pendingUpdateUrl);
            if (!r.success) {
                setStatus(`✗ ${r.message}`, "#fb7185");
                log(`업데이트 실패: ${r.message}`);
                btnLaunch.disabled = false;
                btnCheck.disabled = false;
                return;
            }
            log("업데이트 적용 완료");
            pendingUpdateUrl = null;
            await loadLocalInfo();
        } catch (e) {
            setStatus(`✗ ${e.message}`, "#fb7185");
            log(`업데이트 실패: ${e.message}`);
            btnLaunch.disabled = false;
            btnCheck.disabled = false;
            return;
        }
    }

    setStatus("Aurora 본체 시작 중...", "var(--aurora-cyan)");
    try {
        const r = await Api.launch();
        if (r.success) {
            log("Aurora 시작됨 — Launcher 종료");
            setStatus("✓ Aurora 시작됨", "#34d399");
            // 1초 후 launcher 자동 종료 (사용자가 본체로 자연스럽게 전환)
            setTimeout(() => Api.quit(), 1000);
        } else {
            setStatus(`✗ ${r.message}`, "#fb7185");
            log(`시작 실패: ${r.message}`);
            btnLaunch.disabled = false;
            btnCheck.disabled = false;
        }
    } catch (e) {
        setStatus(`✗ ${e.message}`, "#fb7185");
        log(`시작 실패: ${e.message}`);
        btnLaunch.disabled = false;
        btnCheck.disabled = false;
    }
}

btnCheck.addEventListener("click", checkUpdate);
btnLaunch.addEventListener("click", launchAurora);

// pywebview API 준비될 때까지 대기 — ready 이벤트 후 Api 변수 갱신.
window.addEventListener("pywebviewready", async () => {
    Api = window.pywebview.api;
    await loadLocalInfo();
    setStatus("준비됨 — 시작 버튼 클릭");
    log("Launcher 시작");
    // 자동 update check (시작 시 1회)
    await checkUpdate();
});
