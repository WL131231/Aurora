/* Aurora 랜딩 — 별 파티클 생성 + 작은 인터랙션 + 배경 사진 자동 감지 */

(function () {
    "use strict";

    // ===== 배경 사진 자동 감지 (assets/aurora-bg.jpg 있으면 표시) =====
    const bgImage = document.querySelector(".bg-image");
    if (bgImage) {
        const testImg = new Image();
        testImg.onload = () => bgImage.classList.add("has-image");
        testImg.onerror = () => {
            /* 파일 없음: CSS 오로라 띠 fallback */
        };
        testImg.src = "assets/aurora-bg.jpg";
    }

    // ===== 별 파티클 동적 생성 =====
    const STAR_COUNT = 80;
    const stars = document.getElementById("stars");
    if (stars) {
        const fragment = document.createDocumentFragment();
        for (let i = 0; i < STAR_COUNT; i++) {
            const s = document.createElement("div");
            s.className = "star";
            s.style.left = Math.random() * 100 + "%";
            s.style.top = Math.random() * 100 + "%";
            // 트윙클 timing 분산 (너무 동기화되면 부자연스러움)
            s.style.animationDelay = Math.random() * 4 + "s";
            s.style.animationDuration = 3 + Math.random() * 4 + "s";
            // 일부 별은 크기·투명도 다르게
            const big = Math.random() < 0.15;
            if (big) {
                s.style.width = "3px";
                s.style.height = "3px";
                s.style.boxShadow = "0 0 6px rgba(255, 255, 255, 0.8)";
            }
            fragment.appendChild(s);
        }
        stars.appendChild(fragment);
    }

    // ===== 마우스 시차 효과 (오로라 띠 살짝 따라옴) =====
    const bands = document.querySelectorAll(".aurora-band");
    let mouseX = 0,
        mouseY = 0;
    let targetX = 0,
        targetY = 0;

    document.addEventListener("mousemove", (e) => {
        targetX = (e.clientX / window.innerWidth - 0.5) * 30;
        targetY = (e.clientY / window.innerHeight - 0.5) * 30;
    });

    function animateParallax() {
        // 부드러운 lerp
        mouseX += (targetX - mouseX) * 0.05;
        mouseY += (targetY - mouseY) * 0.05;
        bands.forEach((band, i) => {
            const factor = (i + 1) * 0.5; // 레이어별 다르게 움직임
            band.style.translate = `${mouseX * factor}px ${mouseY * factor}px`;
        });
        requestAnimationFrame(animateParallax);
    }
    animateParallax();
})();
