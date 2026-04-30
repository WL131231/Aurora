// Aurora 프론트엔드 진입점.
// Pywebview 환경에서 로컬 FastAPI(127.0.0.1:8765)를 호출.

const API_BASE = "http://127.0.0.1:8765";

async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/`);
        const data = await res.json();
        document.getElementById("status").textContent = "연결됨";
        document.getElementById("mode").textContent = data.mode || "-";
    } catch (e) {
        document.getElementById("status").textContent = "API 미연결";
        document.getElementById("status").classList.remove("text-emerald-400");
        document.getElementById("status").classList.add("text-rose-400");
    }
}

// 사이드바 탭 전환 (placeholder)
document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active", "bg-zinc-700"));
        btn.classList.add("active", "bg-zinc-700");
    });
});

// 초기 로드
fetchStatus();
