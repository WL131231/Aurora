# Aurora Website Assets

## 🌌 배경 사진 추가하기

### 단계 (3단계, 30초)
1. 원하는 오로라 사진(JPG) 준비
2. 파일명 **`aurora-bg.jpg`** 로 **이 폴더에 저장**
   ```
   website/assets/aurora-bg.jpg
   ```
3. 페이지 새로고침 (Ctrl+F5) → 자동 적용

### 동작 방식
- JS 가 페이지 로드 시 `assets/aurora-bg.jpg` 존재 확인
- 있으면 → ken-burns 애니메이션(천천히 zoom+pan) 으로 배경 표시
- 없으면 → CSS 오로라 띠 fallback (현재 상태)
- CSS 띠는 사진 있을 때 자동으로 톤 다운됨 (충돌 방지)

### 권장 사양
- 해상도: 1920×1080 이상 (4K 도 OK)
- 가로형 (16:9)
- 어두운 배경 + 밝은 오로라 톤 (텍스트 가독성)
- 파일 크기: 1MB 이하 권장 (로딩 속도)

## 무료 이미지 출처
- [Unsplash — aurora](https://unsplash.com/s/photos/aurora-borealis)
- [Pexels — aurora](https://www.pexels.com/search/aurora/)
- [Pixabay — aurora](https://pixabay.com/images/search/aurora/)

저작권 확인 후 사용. Unsplash/Pexels 는 상업·비상업 모두 무료.

## 다른 파일 추가
필요시 이 폴더에 추가 가능 (예: 로고 PNG, favicon 등). 단, 큰 바이너리는
`.gitignore` 검토 필요 (현재 `*.png`, `*.jpg` 등은 무시 안 됨).
