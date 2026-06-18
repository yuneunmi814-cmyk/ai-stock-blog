# 📉 오늘의 저평가 Top 3 — S&P 500 · KOSPI 200

매 영업일 아침, **S&P 500**과 **KOSPI 200** 구성종목 전체를 단순하고 투명한
가치지표만으로 평가해 시장별 **저평가 Top 3**를 뽑아 일자별로 보여줍니다.

🔗 **https://yuneunmi814-cmyk.github.io/ai-stock-blog/**

> ⚠️ PER·PBR·배당수익률 기반의 **단순 정량 스크리닝**일 뿐 투자 추천이 아닙니다.
> 투자 판단과 결과의 책임은 투자자 본인에게 있습니다.

## 선정 방법 (단순·투명)

평가에 쓰는 기초 가치지표는 딱 세 가지입니다.

| 지표 | 의미 | 가중치 | 방향 |
|------|------|:---:|------|
| **PER** | 주가수익비율 | 45% | 낮을수록 저평가 |
| **PBR** | 주가순자산비율 | 35% | 낮을수록 저평가 |
| **배당수익률** | 주가 대비 배당 | 20% | 높을수록 유리 |

1. 각 지수 구성종목의 세 지표를 수집한다. (적자·이상치 제거: `PER 0~60배`, `PBR 0~20배`)
2. 세 지표를 **시장 내 백분위(0~100)** 로 환산한다.
3. `가치점수 = 0.45·PER점수 + 0.35·PBR점수 + 0.20·배당점수` 가 높은 순으로 **Top 3** 선정.

각 종목의 상세 페이지에서 EPS·BPS·ROE·선행 PER·52주 고저·시가총액 등을 함께 제공합니다.

## 데이터 소스 (API 키 불필요)

| 구분 | 구성종목 | 펀더멘털 |
|------|----------|----------|
| 🇺🇸 S&P 500 | Wikipedia | [Yahoo Finance](https://finance.yahoo.com) (`yfinance`) |
| 🇰🇷 KOSPI 200 | 네이버 금융 | [네이버 금융](https://finance.naver.com) 모바일 API |

## 구조

```
ai-stock-blog/
├─ index.html              # 정적 뷰어 (날짜 선택 → 해당일 JSON 로드)
├─ assets/{style.css,app.js}
├─ data/
│  ├─ index.json           # 생성된 날짜 목록
│  └─ YYYY-MM-DD.json       # 일자별 Top 3 (시장별)
├─ scripts/build.py        # 수집·점수·저장 (키 불필요)
├─ requirements.txt
└─ .github/workflows/deploy.yml  # 매일 07:05 KST 빌드 → 커밋 → Pages 배포
```

## 로컬 실행

```bash
pip install -r requirements.txt

# 빠른 점검(시장별 40종목만)
python scripts/build.py --limit 40

# 전체 빌드 (특정 날짜)
python scripts/build.py --date 2026-06-18

# 미리보기
python -m http.server 8000   # → http://localhost:8000/
```

## 자동화

- `.github/workflows/deploy.yml` 이 매 영업일 **07:05 KST**(백업 07:35)에 실행됩니다.
  → `build.py` 로 당일 데이터 생성 → `data/`에 커밋 → 정적 사이트 빌드 → GitHub Pages 배포.
- **Settings → Pages → Source** 는 **GitHub Actions** 로 설정하세요.
- 별도 API 키/시크릿이 필요 없습니다.
