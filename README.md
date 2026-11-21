# 리치부동산 실거래가

서울 25개 구와 화성시 아파트 매매 실거래가를 검색하는 홈페이지예요.

- 주소: https://rich-budongsan.github.io
- 데이터: 국토교통부 아파트 매매 실거래가 (공공데이터포털 API)
- 업데이트: 매일 새벽 5시쯤 GitHub Actions가 자동으로 받아와요 (`.github/workflows/update.yml`)

## 파일 설명
- `index.html`: 검색 화면. 사무실 주소·전화번호는 파일 아래쪽 `CONFIG`에서 바꿀 수 있어요.
- `scripts/fetch.py`: 국토부 API에서 거래를 받아 `data/` 폴더에 저장하는 프로그램
- `scripts/regions.json`: 조회할 지역 코드 목록
- `data/`: 자동으로 만들어지는 데이터 (직접 고치지 마세요)

## 수동으로 업데이트하려면
Actions 탭 → 실거래가 업데이트 → Run workflow
