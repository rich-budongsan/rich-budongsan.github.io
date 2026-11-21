"""국토교통부 아파트 매매 실거래가를 받아 data/ 폴더에 저장합니다.

GitHub Actions가 매일 실행합니다. 필요한 것: 환경변수 MOLIT_API_KEY (Decoding 인증키)
- 처음 실행: 최근 BACKFILL_MONTHS 개월
- 이후 실행: 최근 DAILY_MONTHS 개월만 다시 받아서 덮어씀 (늦게 신고된 거래, 해제 반영)
"""
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
BACKFILL_MONTHS = 24   # 처음에 받을 기간
DAILY_MONTHS = 3       # 매일 다시 받을 기간
KEEP_MONTHS = 36       # 보관 기간 (이보다 오래된 건 삭제)
RECENT_DAYS = 45       # '새 거래' 목록에 보여줄 기간

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
KST = dt.timezone(dt.timedelta(hours=9))


def months_back(n, today):
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def text(item, tag):
    el = item.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def to_int(s, default=0):
    try:
        return int(str(s).replace(",", "").strip())
    except ValueError:
        return default


def call_api(key, code, ym, page):
    q = urllib.parse.urlencode({
        "serviceKey": key, "LAWD_CD": code, "DEAL_YMD": ym,
        "pageNo": page, "numOfRows": 1000,
    })
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(f"{URL}?{q}", timeout=60) as r:
                return r.read()
        except Exception as e:  # 네트워크 오류는 잠시 쉬고 다시
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"API 호출 실패 {code} {ym}: {last}")


def parse(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        body = xml_bytes[:300].decode("utf-8", "replace")
        if "SERVICE_KEY" in body or "Unauthorized" in body or "인증" in body:
            raise ApiError("30", body)
        raise ApiError("parse", body)
    code = text(root, ".//resultCode") or text(root, ".//returnReasonCode")
    msg = text(root, ".//resultMsg") or text(root, ".//returnAuthMsg") or text(root, ".//errMsg")
    if code not in ("", "00", "000"):
        raise ApiError(code, msg)
    total = to_int(text(root, ".//totalCount"), 0)
    return root.findall(".//items/item"), total


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code} {msg}")
        self.code, self.msg = code, msg


def normalize(item, label):
    y, m, d = text(item, "dealYear"), text(item, "dealMonth"), text(item, "dealDay")
    name = text(item, "aptNm")
    umd = text(item, "umdNm")
    jibun = text(item, "jibun")
    sgg = text(item, "sggCd")
    seq = text(item, "aptSeq")
    if not seq:
        seq = "h" + hashlib.md5(f"{sgg}|{umd}|{jibun}|{name}".encode()).hexdigest()[:10]
    return {
        "k": seq.replace("/", "-"),
        "n": name,
        "r": label,
        "u": umd,
        "j": jibun,
        "d": f"{y}-{to_int(m):02d}-{to_int(d):02d}",
        "a": to_int(text(item, "dealAmount")),
        "ar": round(float(text(item, "excluUseAr") or 0), 2),
        "f": to_int(text(item, "floor")),
        "dong": text(item, "aptDong"),
        "by": to_int(text(item, "buildYear")),
        "c": 1 if text(item, "cdealType") else 0,          # 계약 해제
        "g": 1 if "직거래" in text(item, "dealingGbn") else 0,  # 직거래
    }


def fetch_month(key, code, label, ym):
    rows, page = [], 1
    while True:
        items, total = parse(call_api(key, code, ym, page))
        rows += [normalize(i, label) for i in items]
        if len(rows) >= total or not items:
            return rows
        page += 1
        time.sleep(0.3)


def collect(key, regions, months):
    calls = 0
    for reg in regions:
        for ym in months:
            try:
                rows = fetch_month(key, reg["code"], reg["label"], ym)
            except ApiError as e:
                if e.code in ("30", "31", "32", "20", "22", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
                    sys.exit(f"인증키 문제로 중단합니다: {e}. GitHub Settings > Secrets의 MOLIT_API_KEY를 확인하세요.")
                print(f"  건너뜀 {reg['code']} {ym}: {e}")
                continue
            calls += 1
            out = RAW / reg["code"] / f"{ym}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            if rows:
                print(f"  {reg['label']} {reg['code']} {ym}: {len(rows)}건")
            time.sleep(0.2)
    print(f"API 조회 {calls}회 완료")


def build(today):
    keep = set(months_back(KEEP_MONTHS, today))
    trades, seen = [], set()
    for f in sorted(RAW.glob("*/*.json")):
        if f.stem not in keep:
            f.unlink()
            continue
        for t in json.loads(f.read_text(encoding="utf-8")):
            sig = (t["k"], t["d"], t["a"], t["ar"], t["f"], t["dong"], t["c"])
            if sig in seen:
                continue
            seen.add(sig)
            trades.append(t)

    by_cx = {}
    for t in trades:
        by_cx.setdefault(t["k"], []).append(t)

    cdir = DATA / "c"
    cdir.mkdir(parents=True, exist_ok=True)
    index, recent = [], []
    cutoff = (today - dt.timedelta(days=RECENT_DAYS)).isoformat()
    for k, ts in by_cx.items():
        ts.sort(key=lambda t: (t["d"], t["a"]))
        best = {}
        rows = []
        for t in ts:
            key = round(t["ar"])
            hi = 0
            if not t["c"]:
                if key in best and t["a"] > best[key]:
                    hi = 1
                best[key] = max(best.get(key, 0), t["a"])
            rows.append([t["d"], t["a"], t["ar"], t["f"], t["dong"], t["c"], t["g"], hi])
            if t["d"] >= cutoff:
                recent.append([k, t["n"], t["r"], t["u"], t["d"], t["a"], t["ar"], t["f"], t["c"], t["g"], hi])
        rows.reverse()
        last = ts[-1]
        info = {"id": k, "n": last["n"], "r": last["r"], "u": last["u"], "j": last["j"], "by": last["by"], "t": rows}
        (cdir / f"{k}.json").write_text(json.dumps(info, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        valid = [t for t in ts if not t["c"]] or ts
        lt = valid[-1]
        index.append([k, last["n"], last["r"], last["u"], len(ts), lt["d"], lt["a"], lt["ar"]])

    alive = {f"{k}.json" for k in by_cx}
    for f in cdir.glob("*.json"):
        if f.name not in alive:
            f.unlink()

    index.sort(key=lambda r: -r[4])
    recent.sort(key=lambda r: (r[4], r[5]), reverse=True)
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    (DATA / "index.json").write_text(json.dumps({"updated": now, "c": index}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (DATA / "recent.json").write_text(json.dumps({"updated": now, "t": recent[:3000]}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"단지 {len(index)}곳, 거래 {len(trades)}건, 최근 {RECENT_DAYS}일 {len(recent)}건 저장")


def main():
    key = os.environ.get("MOLIT_API_KEY", "").strip()
    if not key:
        sys.exit("MOLIT_API_KEY가 없습니다. GitHub Settings > Secrets and variables > Actions에 등록하세요.")
    regions = json.loads((Path(__file__).parent / "regions.json").read_text(encoding="utf-8"))
    today = dt.datetime.now(KST).date()
    n = to_int(os.environ.get("MONTHS", "0"))
    if n <= 0:
        n = DAILY_MONTHS if (DATA / "index.json").exists() else BACKFILL_MONTHS
    months = months_back(n, today)
    print(f"{len(regions)}개 지역 x {n}개월 조회 ({months[-1]}~{months[0]})")
    collect(key, regions, months)
    build(today)


if __name__ == "__main__":
    main()
