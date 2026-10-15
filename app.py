"""
가천대학교 건설환경공학과 · 모바일 동문 수첩
=================================================
실행:  streamlit run app.py

구성
  - 데이터 계층 : load_data() / save_data()  ← 구글 시트·DB로 바꿀 때 이 두 함수만 교체
  - Tab 1      : 📋 동문 수첩 보기 (통계, 검색·필터·정렬, 카드/표 보기)
  - Tab 2      : ✏️ 내 정보 등록/수정 (이름+학번 기준 Upsert, 기존 정보 불러오기)
  - Tab 3      : ⚙️ 정보 삭제 / 관리 (본인 삭제, 방장 전체 명단 다운로드)

보안 메모
  - 비밀번호(PIN)는 원문 그대로 저장하지 않고 SHA-256 해시로 저장한다.
    (CSV/시트를 누가 열어 봐도 PIN이 바로 보이지 않게 하기 위함)
  - 방장 비밀번호(ADMIN_PIN)는 배포 시 st.secrets 로 옮기는 것을 권장한다.
    secrets 가 없으면 기본값 "9999" 를 사용한다.
"""

import hashlib
import io
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────
# 0. 기본 설정
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="건설환경공학과 동문 수첩",
    page_icon="🎓",
    layout="centered",          # 모바일에서는 centered 가 가장 보기 편함
)

DATA_FILE = "alumni_data.csv"

COLUMNS = ["이름", "학번", "현재분야", "소속_직무", "한줄인사", "연락처", "비밀번호", "업데이트일시"]
FIELDS = ["학부 재학", "시공사", "설계/엔지니어링", "공기업/공무원", "대학원/연구", "기타"]

KST = timezone(timedelta(hours=9))  # 서버(Streamlit Cloud)는 UTC 이므로 한국 시간으로 고정


def _secret(key: str, default: str) -> str:
    """st.secrets 에 값이 있으면 사용하고, secrets 파일이 없거나 키가 없으면 기본값을 쓴다."""
    try:
        return str(st.secrets[key])
    except Exception:
        return default


ADMIN_PIN = _secret("ADMIN_PIN", "9999")                 # 방장 마스터 비밀번호
PIN_SALT = _secret("PIN_SALT", "gachon-cee-alumni")      # PIN 해시용 솔트

# 카카오톡 인앱 브라우저용 여백 축소
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.2rem; padding-left: 1rem; padding-right: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────
# 1. 데이터 계층 (저장소를 바꿀 때는 이 두 함수만 수정)
# ─────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    """동문 데이터를 읽어 COLUMNS 순서의 DataFrame(모든 값 문자열)으로 반환한다."""
    try:
        # dtype=str : 학번 "05" 가 숫자 5 로 바뀌는 것 방지
        # keep_default_na=False : 빈 칸을 NaN 이 아닌 "" 로 읽음
        df = pd.read_csv(DATA_FILE, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except FileNotFoundError:
        return pd.DataFrame(columns=COLUMNS)

    for col in COLUMNS:                 # 컬럼이 빠진 파일도 안전하게 처리
        if col not in df.columns:
            df[col] = ""
    return df[COLUMNS].reset_index(drop=True)


def save_data(df: pd.DataFrame) -> None:
    """DataFrame 전체를 저장한다. 임시 파일에 쓴 뒤 교체해 저장 중 파일이 깨지는 것을 막는다."""
    import os

    tmp = DATA_FILE + ".tmp"
    df[COLUMNS].to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, DATA_FILE)


# ─────────────────────────────────────────────────────────────
# 2. 공통 도우미 함수
# ─────────────────────────────────────────────────────────────
def hash_pin(pin: str) -> str:
    """PIN을 솔트와 함께 SHA-256 해시로 변환한다."""
    return hashlib.sha256((PIN_SALT + pin).encode("utf-8")).hexdigest()


def pin_matches(stored_hash: str, pin: str) -> bool:
    """입력 PIN이 저장된 PIN 또는 방장 비밀번호와 일치하는지 확인한다."""
    return pin == ADMIN_PIN or hash_pin(pin) == stored_hash


def find_rows(df: pd.DataFrame, name: str, hakbun: str) -> list:
    """이름 + 학번이 일치하는 행 번호 목록을 반환한다."""
    return df.index[(df["이름"] == name) & (df["학번"] == hakbun)].tolist()


def validate_identity(name: str, hakbun: str, pin: str) -> list:
    """이름·학번·PIN 공통 검증. 오류 메시지 목록을 반환(비어 있으면 통과)."""
    errors = []
    if not name:
        errors.append("이름을 입력해 주세요.")
    if not re.fullmatch(r"\d{2}", hakbun):
        errors.append("학번은 입학 연도 **숫자 2자리만** 입력해 주세요. (예: 20학번 → `20`)")
    if not re.fullmatch(r"\d{4}", pin):
        errors.append("비밀번호는 **숫자 4자리**로 입력해 주세요.")
    return errors


def entry_year(hakbun: str) -> int:
    """학번 2자리를 실제 입학 연도로 변환 (예: 98 → 1998, 05 → 2005). 정렬용."""
    y = int(hakbun) if hakbun.isdigit() else 0
    this_yy = datetime.now(KST).year % 100
    return 2000 + y if y <= this_yy else 1900 + y


def md_escape(text: str) -> str:
    """사용자가 입력한 글이 마크다운으로 해석되지 않도록 특수문자를 이스케이프한다."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~<])", r"\\\1", str(text))


def today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────
# 3. 화면 상단: 제목 + 직전 작업 결과 메시지
# ─────────────────────────────────────────────────────────────
st.title("🎓 건설환경공학과 동문 수첩")
st.caption("가천대학교 건설환경공학과 동문 연락망 · 본인 정보만 등록·수정해 주세요")

# st.rerun() 후에는 직전 메시지가 사라지므로, session_state 에 담아 두었다가 여기서 보여준다.
flash = st.session_state.pop("flash", None)
if flash:
    kind, msg = flash
    st.success(msg)
    if kind == "new":
        st.balloons()

tab_view, tab_edit, tab_admin = st.tabs(["📋 동문 수첩", "✏️ 등록/수정", "⚙️ 삭제/관리"])


# ─────────────────────────────────────────────────────────────
# Tab 1. 동문 수첩 보기
# ─────────────────────────────────────────────────────────────
with tab_view:
    df = load_data()

    # ── 통계 요약 ──
    counts = df["현재분야"].value_counts()
    st.metric("등록된 동문", f"{len(df)}명")
    st.markdown(" · ".join(f"{f} **{int(counts.get(f, 0))}**" for f in FIELDS))

    if df.empty:
        st.info("아직 등록된 동문이 없습니다. **✏️ 등록/수정** 탭에서 첫 번째로 등록해 주세요!")
    else:
        # ── 검색 · 필터 · 정렬 ──
        keyword = st.text_input(
            "🔍 검색",
            placeholder="이름, 학번, 소속, 인사말 키워드",
            help="입력 후 엔터(또는 키보드 완료)를 누르면 바로 반영됩니다.",
        ).strip()
        field_filter = st.multiselect("분야 필터", FIELDS, placeholder="전체 분야")
        sort_by = st.selectbox(
            "정렬",
            ["학번 오름차순 (선배부터)", "학번 내림차순 (후배부터)", "이름 가나다순", "최신 등록순"],
        )
        view_mode = st.radio("보기 방식", ["📱 카드 보기", "📊 표 보기"], horizontal=True)

        view = df.copy()
        view["_순서"] = range(len(view))          # 원래 등록 순서 (최신순 정렬의 동점 처리용)
        view["_입학연도"] = view["학번"].map(entry_year)

        # 키워드 검색 (대소문자 무시, 정규식 해석 안 함)
        if keyword:
            search_cols = ["이름", "학번", "소속_직무", "한줄인사"]
            mask = view[search_cols].apply(
                lambda col: col.str.contains(keyword, case=False, regex=False)
            ).any(axis=1)
            view = view[mask]

        # 분야 필터 (아무것도 고르지 않으면 전체)
        if field_filter:
            view = view[view["현재분야"].isin(field_filter)]

        # 정렬
        if sort_by.startswith("학번 오름차순"):
            view = view.sort_values(["_입학연도", "이름"])
        elif sort_by.startswith("학번 내림차순"):
            view = view.sort_values(["_입학연도", "이름"], ascending=[False, True])
        elif sort_by == "이름 가나다순":
            view = view.sort_values("이름")
        else:  # 최신 등록순: 업데이트일시 → 같은 날이면 나중에 등록된 사람 먼저
            view = view.sort_values(["업데이트일시", "_순서"], ascending=False)

        st.caption(f"검색 결과 {len(view)}명")

        if view.empty:
            st.warning("조건에 맞는 동문이 없습니다.")
        elif view_mode == "📱 카드 보기":
            # ── 모바일 카드 뷰 ──
            for _, r in view.iterrows():
                with st.container(border=True):
                    affil = f" ({md_escape(r['소속_직무'])})" if r["소속_직무"] else ""
                    st.markdown(
                        f"**[{md_escape(r['학번'])}] {md_escape(r['이름'])}** | "
                        f"{md_escape(r['현재분야'])}{affil}"
                    )
                    st.markdown(f"💬 {md_escape(r['한줄인사'])}")
                    if r["연락처"]:
                        st.caption(f"📮 {md_escape(r['연락처'])}")
        else:
            # ── 전체 표 뷰 (비밀번호·내부 컬럼 제외) ──
            show_cols = ["학번", "이름", "현재분야", "소속_직무", "한줄인사", "연락처", "업데이트일시"]
            st.dataframe(view[show_cols], hide_index=True)


# ─────────────────────────────────────────────────────────────
# Tab 2. 내 정보 등록 / 수정 (Upsert)
# ─────────────────────────────────────────────────────────────
with tab_edit:
    st.markdown(
        "**처음이면** 아래 양식을 채워 저장하면 등록됩니다.  \n"
        "**이미 등록했다면** 같은 이름·학번과 비밀번호로 저장하면 내용이 수정됩니다."
    )

    # ── 기존 정보 불러오기: 성공하면 아래 양식의 기본값이 채워짐 ──
    with st.expander("🔄 이미 등록했나요? 기존 정보 불러오기"):
        with st.form("lookup_form"):
            l_name = st.text_input("이름", key="l_name")
            l_hakbun = st.text_input("학번 (2자리)", max_chars=2, key="l_hakbun")
            l_pin = st.text_input("비밀번호 (4자리)", type="password", max_chars=4, key="l_pin")
            lookup = st.form_submit_button("불러오기", use_container_width=True)

        if lookup:
            l_name, l_hakbun, l_pin = l_name.strip(), l_hakbun.strip(), l_pin.strip()
            errs = validate_identity(l_name, l_hakbun, l_pin)
            if errs:
                for e in errs:
                    st.error(e)
            else:
                cur = load_data()
                rows = find_rows(cur, l_name, l_hakbun)
                if not rows:
                    st.error("해당 이름·학번으로 등록된 정보가 없습니다.")
                elif not pin_matches(cur.at[rows[0], "비밀번호"], l_pin):
                    st.error("비밀번호가 일치하지 않습니다.")
                else:
                    row = cur.loc[rows[0]].to_dict()
                    row.pop("비밀번호", None)          # 해시값은 화면으로 넘기지 않음
                    st.session_state["prefill"] = row
                    st.success("기존 정보를 불러왔습니다. 아래 양식에서 고친 뒤 저장하세요.")

    prefill = st.session_state.get("prefill", {})

    # ── 등록/수정 양식 ──
    # 위젯에 key 를 주지 않았으므로, prefill 이 바뀌면 value(기본값)가 새로 반영된다.
    with st.form("upsert_form"):
        name = st.text_input("이름 *", value=prefill.get("이름", ""), max_chars=20, placeholder="홍길동")
        hakbun = st.text_input(
            "학번 * (입학 연도 숫자 2자리)",
            value=prefill.get("학번", ""),
            max_chars=2,
            placeholder="예: 20",
        )
        field_default = prefill.get("현재분야", FIELDS[0])
        field = st.selectbox(
            "현재 분야 *",
            FIELDS,
            index=FIELDS.index(field_default) if field_default in FIELDS else 0,
        )
        affil = st.text_input(
            "소속 / 직무 (선택)",
            value=prefill.get("소속_직무", ""),
            max_chars=50,
            placeholder="예: OO건설 시공, 구조연구실",
        )
        greeting = st.text_area(
            "한 줄 인사 *",
            value=prefill.get("한줄인사", ""),
            max_chars=150,
            placeholder="동문들에게 한마디!",
        )
        contact = st.text_input(
            "연락처 (선택, 공개 가능한 것만)",
            value=prefill.get("연락처", ""),
            max_chars=100,
            placeholder="이메일, 링크드인, 인스타그램 등",
        )
        pin = st.text_input(
            "비밀번호 * (숫자 4자리)",
            type="password",
            max_chars=4,
            help="신규 등록 시 정한 4자리가 이후 수정·삭제할 때 필요합니다. 잊지 마세요!",
        )
        submitted = st.form_submit_button("💾 저장하기", type="primary", use_container_width=True)

    if submitted:
        # 입력값 정리
        name, hakbun, pin = name.strip(), hakbun.strip(), pin.strip()
        affil, greeting, contact = affil.strip(), greeting.strip(), contact.strip()

        errors = validate_identity(name, hakbun, pin)
        if not greeting:
            errors.append("한 줄 인사를 입력해 주세요.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            cur = load_data()
            rows = find_rows(cur, name, hakbun)

            if not rows:
                # ── 신규 등록 (Create) ──
                new_row = {
                    "이름": name, "학번": hakbun, "현재분야": field, "소속_직무": affil,
                    "한줄인사": greeting, "연락처": contact,
                    "비밀번호": hash_pin(pin), "업데이트일시": today(),
                }
                cur = pd.concat([cur, pd.DataFrame([new_row])], ignore_index=True)
                save_data(cur)
                st.session_state.pop("prefill", None)
                st.session_state["flash"] = ("new", f"🎉 {name}({hakbun}학번) 님, 등록되었습니다!")
                st.rerun()
            else:
                i = rows[0]
                if not pin_matches(cur.at[i, "비밀번호"], pin):
                    st.error(
                        "이미 등록된 이름·학번입니다. 비밀번호가 일치하지 않습니다.  \n"
                        "본인이 맞다면 비밀번호를 확인하고, 동명이인이거나 비밀번호를 잊었다면 방장에게 문의해 주세요."
                    )
                else:
                    # ── 기존 정보 수정 (Update) ──  비밀번호는 기존 값 유지
                    cur.loc[i, ["현재분야", "소속_직무", "한줄인사", "연락처", "업데이트일시"]] = [
                        field, affil, greeting, contact, today()
                    ]
                    save_data(cur)
                    st.session_state.pop("prefill", None)
                    st.session_state["flash"] = ("update", f"✅ {name}({hakbun}학번) 님의 정보가 수정되었습니다.")
                    st.rerun()

    st.caption("※ 이름이나 학번을 잘못 입력했다면 ⚙️ 탭에서 삭제 후 다시 등록해 주세요.")


# ─────────────────────────────────────────────────────────────
# Tab 3. 정보 삭제 / 방장 관리
# ─────────────────────────────────────────────────────────────
with tab_admin:
    # ── 본인 정보 삭제 ──
    st.subheader("🗑️ 내 정보 삭제")
    with st.form("delete_form"):
        d_name = st.text_input("이름")
        d_hakbun = st.text_input("학번 (2자리)", max_chars=2)
        d_pin = st.text_input("비밀번호 (4자리)", type="password", max_chars=4)
        d_confirm = st.checkbox("삭제하면 되돌릴 수 없음을 확인했습니다.")
        delete = st.form_submit_button("삭제하기", use_container_width=True)

    if delete:
        d_name, d_hakbun, d_pin = d_name.strip(), d_hakbun.strip(), d_pin.strip()
        errs = validate_identity(d_name, d_hakbun, d_pin)
        if not d_confirm:
            errs.append("삭제 확인란에 체크해 주세요.")
        if errs:
            for e in errs:
                st.error(e)
        else:
            cur = load_data()
            rows = find_rows(cur, d_name, d_hakbun)
            if not rows:
                st.error("해당 이름·학번으로 등록된 정보가 없습니다.")
            elif not pin_matches(cur.at[rows[0], "비밀번호"], d_pin):
                st.error("비밀번호가 일치하지 않습니다.")
            else:
                cur = cur.drop(index=rows).reset_index(drop=True)
                save_data(cur)
                st.session_state.pop("prefill", None)
                st.session_state["flash"] = ("delete", f"{d_name}({d_hakbun}학번) 님의 정보가 삭제되었습니다.")
                st.rerun()

    st.divider()

    # ── 방장 전용: 전체 명단 다운로드 ──
    st.subheader("🔑 방장 관리")
    admin_pin = st.text_input("방장 비밀번호", type="password", max_chars=4, key="admin_pin")

    if admin_pin:
        if admin_pin != ADMIN_PIN:
            st.error("방장 비밀번호가 아닙니다.")
        else:
            all_df = load_data()
            export = all_df.drop(columns=["비밀번호"])      # 다운로드 파일에 PIN 해시는 넣지 않음
            stamp = datetime.now(KST).strftime("%Y%m%d")
            st.success(f"방장 인증 완료 · 전체 {len(export)}명")

            # CSV (엑셀에서 한글이 깨지지 않도록 UTF-8-SIG)
            st.download_button(
                "📥 CSV 다운로드",
                data=export.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"동문명단_{stamp}.csv",
                mime="text/csv",
                use_container_width=True,
            )

            # 엑셀(.xlsx)
            buf = io.BytesIO()
            export.to_excel(buf, index=False, sheet_name="동문명단")
            st.download_button(
                "📥 엑셀(.xlsx) 다운로드",
                data=buf.getvalue(),
                file_name=f"동문명단_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            st.caption("방장 비밀번호로는 위 '내 정보 삭제'·'등록/수정'에서 누구의 정보든 수정·삭제할 수 있습니다.")
