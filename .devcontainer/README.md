# Aurora Dev Container

GitHub Codespaces 또는 로컬 VS Code Dev Container 에서 사용.

## Codespaces (브라우저에서)
1. GitHub 저장소 페이지: https://github.com/WL131231/Aurora
2. **Code** 버튼 → **Codespaces** 탭 → **Create codespace on main**
3. 1~2분 대기 (자동으로 Python 3.11 + 의존성 설치)
4. 끝. VS Code 환경 그대로 브라우저에 뜸.

## 로컬 VS Code Dev Container
1. Docker Desktop 설치
2. VS Code + "Dev Containers" 확장 설치
3. VS Code 에서 폴더 열고 → 명령 팔레트 (Ctrl+Shift+P) → "Reopen in Container"

## 환경 내용
- Python 3.11 (공식 devcontainer 이미지)
- 의존성 자동 설치 (`requirements-dev.txt` + `pip install -e .`)
- VS Code 확장 자동 설치:
  - Python + Pylance (타입 추론)
  - Ruff (린팅·포매팅)
  - GitHub Pull Request (PR 검토)
  - YAML, TOML 지원
- 저장 시 자동 포맷 + import 정렬 (Ruff)
- pytest 통합

## 코드 검증 명령
```bash
ruff check .              # 린트
pytest                    # 테스트
ruff check --fix .        # 자동 수정
```

## ⚠️ Codespaces 비용 관리
- 개인 무료: 월 120 core-hr (= 2-core 기준 60시간 실시간)
- 초과 방지: GitHub Settings → Billing → Spending limit → **$0** 설정
- 안 쓸 땐 Codespace 정지 (자동 30분 후 정지)
