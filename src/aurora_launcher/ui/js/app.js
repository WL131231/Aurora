// Aurora Launcher GUI v0.1.16 — Start 단일 흐름.
// 사용자가 START 클릭 → 자동 update check → has_update 면 download+swap → 본체 실행.

let Api = null;

const versionInfo = document.getElementById("version-info");
const statusLine = document.getElementById("status-line");
const logList = document.getElementById("log-list");
const btnStart = document.getElementById("btn-start");

function log(msg) {
    const li = document.createElement("li");
    li.textContent = msg;
    logList.appendChild(li);
    logList.parentElement.scrollTop = logList.parentElement.scrollHeight;
}

function setStatus(text, color) {
    statusLine.textContent = text;
    statusLine.style.color = color || "var(--text-3)";
}

async function loadVersionInfo() {
    if (!Api) return;
    try {
        const local = await Api.get_local_version();
        const launcherV = await Api.get_launcher_version();
        const localStr = (local && local !== "unknown") ? `v${local}` : "—";
        versionInfo.textContent = `Launcher v${launcherV} · 본체 ${localStr}`;
    } catch (e) {
        log(`정보 조회 실패: ${e.message}`);
    }
}

// START 버튼 — 단일 흐름: check → (필요시) download+swap → launch
async function startFlow() {
    if (!Api) return;
    btnStart.disabled = true;

    // 1. 업데이트 체크
    setStatus("업데이트 확인 중...", "var(--text-2)");
    log("업데이트 확인 시작");
    let checkResult;
    try {
        checkResult = await Api.check_update();
    } catch (e) {
        setStatus(`✗ 체크 실패: ${e.message} — 본체만 실행`, "#fb7185");
        log(`체크 실패: ${e.message}`);
        await launchOnly();
        return;
    }

    if (checkResult.error) {
        // 네트워크 실패 — 본체 그대로 실행
        setStatus(`⚠ ${checkResult.error} — 본체 그대로 실행`, "#fbbf24");
        log(checkResult.error);
        await launchOnly();
        return;
    }

    // 2. has_update = true 면 다운 + swap
    if (checkResult.has_update && checkResult.url) {
        setStatus(`최신 ${checkResult.latest} 다운로드 중...`, "var(--text-2)");
        log(`업데이트 ${checkResult.latest} 발견`);
        try {
            const swap = await Api.download_and_swap(checkResult.url);
            if (!swap.success) {
                setStatus(`✗ ${swap.message} — 기존 본체 실행`, "#fb7185");
                log(`업데이트 실패: ${swap.message}`);
            } else {
                log("업데이트 적용 완료");
                await loadVersionInfo();
            }
        } catch (e) {
            setStatus(`✗ ${e.message}`, "#fb7185");
            log(`업데이트 실패: ${e.message}`);
        }
    } else {
        log(`최신 버전 (${checkResult.latest})`);
    }

    // 3. 본체 실행
    await launchOnly();
}

async function launchOnly() {
    setStatus("Aurora 시작 중...", "var(--text-2)");
    try {
        const r = await Api.launch();
        if (r.success) {
            log("Aurora 시작 — Launcher 종료");
            setStatus("✓ Aurora 시작됨", "#34d399");
            setTimeout(() => Api.quit(), 1200);
        } else {
            setStatus(`✗ ${r.message}`, "#fb7185");
            log(`시작 실패: ${r.message}`);
            btnStart.disabled = false;
        }
    } catch (e) {
        setStatus(`✗ ${e.message}`, "#fb7185");
        log(`시작 실패: ${e.message}`);
        btnStart.disabled = false;
    }
}

btnStart.addEventListener("click", startFlow);

window.addEventListener("pywebviewready", async () => {
    Api = window.pywebview.api;
    await loadVersionInfo();
    log("Launcher 시작");
});
