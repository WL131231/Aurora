# 🔒 Aurora 팀원 보안 셋업 가이드

**대상**: Aurora 팀원 (정용우, ChoYoon, WooJae)
**소요 시간**: 약 20분
**최종 개정**: 2026-05-10 (v0.2.21)

---

## ❓ 왜 박는지

Aurora 측 사용자 머신 측 자동 download + 실행 박는 자동매매 봇입니다. 누가 본인 GitHub 계정 측 hijack 박으면 → 악성 release push → 사용자 머신 측 악성 코드 실행 → **사용자 자금 측 탈취 가능**.

**그래서:**
- 본인 GitHub 측 누가 들어와도 → **악성 commit / release push X** 박게 박음
- 본인 노트북 분실 박아도 → **GPG private key 측 X 라 main 머지 X**

**박을 4가지 (단계 별로 박음):**

| 단계 | 박을 거 | 시간 | 효과 |
|------|---------|------|------|
| 1 | GitHub 2FA | 5분 | 비밀번호 + 휴대폰 코드 둘 다 있어야 로그인 |
| 2 | GPG key 생성 | 5분 | 본인 commit 측 디지털 서명 박음 |
| 3 | Git config | 2분 | 자동 서명 박힘 |
| 4 | (선택) Bybit API key 권한 정정 | 5분 | API key leak 시 자금 인출 차단 |

---

## 📌 1. GitHub 2FA 활성화 (5분)

### 박는 이유
> 누가 비밀번호 알아내도 → 휴대폰 측 6자리 코드 측 X → 로그인 자체 X.

### 박는 방법

**(a) 인증 앱 설치** (3가지 중 하나)
- 📱 [Google Authenticator](https://play.google.com/store/apps/details?id=com.google.android.apps.authenticator2) (Android / iOS)
- 📱 [Authy](https://authy.com/) (멀티 디바이스 동기화)
- 📱 [1Password](https://1password.com/) (비밀번호 관리 + 2FA 통합)

**(b) GitHub 측 활성화**
1. https://github.com/settings/security 접속
2. "Two-factor authentication" 측 **Enable two-factor authentication** 클릭
3. **Set up using an app** 선택 (SMS X — 보안 ↓)
4. QR 코드 측 위 인증 앱 측 스캔
5. 앱 측 6자리 코드 측 GitHub 측 입력

**(c) Recovery codes 저장** ⭐
- 16자리 코드 8개 박힘 → **반드시 안전한 곳 보관**
- 추천: 비밀번호 관리자 (1Password / Bitwarden) 또는 종이 출력
- ⚠️ 휴대폰 분실 시 본 코드 측 X 면 계정 복구 X

### 검증
- GitHub 로그아웃 → 다시 로그인 → 비밀번호 + 6자리 코드 둘 다 요구되면 OK ✅

---

## 🔑 2. GPG Key 생성 (5분)

### 박는 이유
> 누가 GitHub 측 hijack 박아도 → 본인 GPG private key 측 본인 노트북 측만 박혀있어 → signed commit 측 못 박음 → main 머지 X.

### 사전 준비

**Windows**: Git for Windows 설치 측 GPG 측 박혀있음. 검증:
```bash
# Git Bash 측 박음
gpg --version
```
→ `gpg (GnuPG) 2.x.x` 박혀나오면 OK. 안 박혀있으면 [Git for Windows](https://git-scm.com/download/win) 또는 [Gpg4win](https://www.gpg4win.org/) 설치.

**macOS**: 
```bash
brew install gnupg
gpg --version
```

**Linux**: 보통 기본 설치. `sudo apt install gnupg2` 박음.

### 박는 방법

**(a) GPG key 생성** (Git Bash / 터미널 측)

```bash
gpg --full-generate-key
```

대화형 입력 — 다음 답변:

| 질문 | 답변 |
|------|------|
| `Please select what kind of key you want:` | **`9`** Enter (ECC sign+encrypt, default) |
| `Please select which elliptic curve you want:` | **`1`** Enter (Curve 25519) |
| `Key is valid for? (0)` | **빈 Enter** (never expire) |
| `Is this correct? (y/N)` | **`y`** Enter |
| `Real name:` | 본인 이름 (실명 또는 GitHub username) |
| `Email address:` | **GitHub 등록 이메일 ⭐** (정확히 일치 박아야) |
| `Comment:` | 빈 Enter |
| `Change ... or (O)kay/(Q)uit?` | **`O`** Enter |

→ **passphrase 입력** 창 박힘. 강한 passphrase 박음 (12자+ 추천) + **반드시 안전 보관** ⭐ (비밀번호 관리자 측).

생성 박힌 후:

```
pub   ed25519 2026-05-10 [SC]
      ABCDEF1234567890ABCDEF1234567890ABCDEF12   ← 이건 fingerprint (40자)
uid   본인이름 <email@example.com>
sub   cv25519 2026-05-10 [E]
```

**(b) KEY_ID 확인**

```bash
gpg --list-secret-keys --keyid-format=long
```

출력 측 `sec   ed25519/XXXXXXXXXXXXXXXX` — **마지막 16자리 = KEY_ID**. 복사 박음.

**(c) Public key 출력 + 복사**

```bash
gpg --armor --export <KEY_ID>
```

출력 측 이런 형식:

```
-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEZxxx... (긴 base64, 30~40줄)
...
=AB12
-----END PGP PUBLIC KEY BLOCK-----
```

→ **`-----BEGIN`** 부터 **`-----END...-----`** 까지 **전체** 마우스 드래그 + 우클릭 → Copy.

**(d) GitHub 측 등록**

1. https://github.com/settings/gpg/new 접속
2. **Title**: `Aurora 작업용` (또는 자유)
3. **Key**: 위 복사 측 paste
4. **Add GPG key** 클릭

✅ 등록 박힘 → GPG keys 페이지 측 본인 key 측 박혀있어야 OK.

---

## ⚙️ 3. Git Config 박음 (2분)

### 박는 이유
> Git 측 본인 commit 측 자동 GPG 서명 박게 박음. 매번 `-S` 플래그 박을 필요 X.

### 박는 방법 (Git Bash / 터미널 측)

```bash
# KEY_ID 측 위 (b) 측 복사 측 16자리 박음
git config --global user.signingkey <KEY_ID>
git config --global commit.gpgsign true
git config --global tag.gpgsign true

# user.email 측 GPG key 측 등록한 이메일 측 정확히 일치 박아야 ⭐
git config --global user.email "본인 GitHub 이메일"
git config --global user.name "본인 이름"
```

### 검증

Aurora repo 측 측 test commit 박음:

```bash
cd /path/to/Aurora
git checkout main
git pull origin main
git commit --allow-empty -m "test: GPG signed commit verify (본인 이름)"
```

→ passphrase 입력 창 박힘 (또는 자동 박힘 if cached). 박은 후:

```bash
git log --show-signature -1
```

→ 출력 측 **`Good signature from "본인이름 <email>" [ultimate]`** 박혀있으면 OK ✅

push:
```bash
git push origin main
```

⚠️ branch protection 측 박혀있어 직접 main push 측 차단됨 — PR 통해 박음. 위 test 측 PR 박을 필요 X (commit 측 그대로 둬도 OK).

---

## 💼 4. (선택) Bybit API Key 권한 정정 (5분)

### 박는 이유
> Bybit Demo Trading 박혀있어 실 자금 X — **현재 측 skip 가능**. 단 Phase 3 실거래 측 박을 때 **반드시 박아야**.

### 박는 방법 (Phase 3 측 박을 때)

1. https://www.bybit.com/app/user/api-management 접속
2. 기존 API key 측 **Edit** 클릭
3. 권한 체크:
   - ☑ **Read** (필수)
   - ☑ **Trade** — Unified Trading (필수)
   - ❌ **Withdraw** — **반드시 X** ⭐ (leak 시 자금 인출 차단)
4. (선택) IP restriction — 본인 IP 박음 (가능 시)
5. Save

### 검증
- Aurora 봇 측 정상 작동 박는지 확인 (read + trade 측 충분)

---

## ✅ 검증 체크리스트

박은 후 본인 측 확인:

- [ ] GitHub https://github.com/settings/security 측 **2FA Enabled** 박혀있음
- [ ] Recovery codes 측 안전한 곳 보관 박힘
- [ ] GitHub https://github.com/settings/keys 측 GPG key 박혀있음
- [ ] 본인 commit 측 GitHub 측 **"Verified" 초록 배지** 박혀있음
- [ ] (Phase 3 시) Bybit API key 측 Withdraw 권한 X 박힘

---

## ❓ 자주 묻는 질문

### Q1. "Verified" 배지 측 안 박혀요
- A: GPG key email 측 GitHub 등록 email 측 정확 일치 박혔는지 확인 (`gpg --list-secret-keys`).
- A: `git config --global user.email` 측 GPG email 측 동일 박혔는지 확인.

### Q2. passphrase 측 매번 물어봐요
- A: GPG agent 측 passphrase cache 박음. Git Bash 측:
   ```bash
   echo "default-cache-ttl 28800" >> ~/.gnupg/gpg-agent.conf
   echo "max-cache-ttl 28800" >> ~/.gnupg/gpg-agent.conf
   gpg-connect-agent reloadagent /bye
   ```
   → 8시간 측 cache 박힘.

### Q3. 노트북 측 잃어버리면?
- A: GitHub 측 GPG key 측 revoke 박음. 새 key 측 박는 절차 측 위 #2 ~ #3 측 그대로 박음.
- A: GitHub 2FA recovery codes 측 측 본인 보관 박혀있어야 계정 복구 박힘.

### Q4. private key 측 백업 박을 수 있나요?
- A: 박을 수 있음. 단 **매우 신중**:
   ```bash
   gpg --export-secret-keys --armor <KEY_ID> > private-key-backup.asc
   ```
   → 본 파일 측 **암호화 박힌 USB** 또는 **password manager 측 보관**. **GitHub / 이메일 / 클라우드 절대 X**.

---

## 🆘 문제 발생 시

장수 (사용자) 또는 오터(Claude) 측 문의 박음. 단계 별 진행 상황 + 스크린샷 박으면 빨리 박힘.

---

**관련 문서**:
- [이용약관](legal/TERMS_OF_SERVICE.md)
- [개인정보처리방침](legal/PRIVACY_POLICY.md)
- [환불정책](legal/REFUND_POLICY.md)
