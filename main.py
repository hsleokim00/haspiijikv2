import streamlit as st
import requests
import math

# ---------------- 기본 설정 ----------------
st.set_page_config(
    page_title="피이직대학 이직 상담소",
    page_icon="💼",
    layout="wide",
)

# ---------------- 상태 초기화 ----------------
if "page" not in st.session_state:
    st.session_state.page = "input"

if "decision" not in st.session_state:
    st.session_state.decision = None

if "Wp" not in st.session_state:
    st.session_state.Wp = None

if "Wk" not in st.session_state:
    st.session_state.Wk = None


# ---------------- Cloudflare Worker API (DART 연동) ----------------
API_BASE = "https://black-bread-33be.dlspike520.workers.dev/"


def fetch_corp_metrics(corp_name: str):
    corp = corp_name.strip()
    if not corp:
        return None

    try:
        url = f"{API_BASE}?corp={corp}"
        r = requests.get(url, timeout=8)
        data = r.json()

        if not data.get("ok"):
            return None
        return data.get("metrics", {})
    except:
        return None


# ---------------- 산업별 평균 연봉 상승률 ----------------
INDUSTRY_GROWTH = {
    "서비스업": 0.011,
    "제조·화학업": 0.03,
    "판매·유통업": 0.043,
    "의료·제약업": 0.027,
    "IT·통신업": 0.043,
}


def get_industry_growth(industry):
    return INDUSTRY_GROWTH.get(industry, 0.03)


# ---------------- 회사 계수 계산식 ----------------
def compute_company_factor(metrics, fallback_growth):
    if metrics is None:
        sg = fallback_growth
    else:
        sg = metrics.get("salesGrowth", fallback_growth)

    # 매출 성장률 반영
    growth_component = 1 + sg

    # 자산 규모 반영 (log10 활용)
    size_component = 1
    if metrics and isinstance(metrics.get("assets"), (int, float)) and metrics["assets"] > 0:
        try:
            lg = math.log10(metrics["assets"])
            size_component = lg / 12
        except:
            size_component = 1

    return growth_component * size_component


# ---------------- 페이지 1 (직종 선택 + 기본 입력) ----------------
def page_input():
    st.title("피이직대학 이직 상담소")
    st.subheader("1단계: 직종 선택 및 기본 정보 입력")

    colA, colB = st.columns(2)

    with colA:
        st.markdown("### 현재 직종")
        current_industry = st.selectbox(
            "현재 직종",
            ["", "서비스업", "제조·화학업", "판매·유통업", "IT·통신업", "의료·제약업"],
            key="cur_ind",
        )

    with colB:
        st.markdown("### 이직 고려 직종")
        target_industry = st.selectbox(
            "이직 고려 직종",
            ["", "서비스업", "제조·화학업", "판매·유통업", "IT·통신업", "의료·제약업"],
            key="tgt_ind",
        )

    st.markdown("---")
    st.markdown("### 개인 기본 정보 입력")

    years = st.number_input("연차", min_value=0.0, step=1.0, key="years")
    salary = st.number_input("현재 연봉(만원 단위)", min_value=0, step=100, key="salary")

    corp_now = st.text_input("현재 회사명", key="corp_now")
    corp_next = st.text_input("이직 고려 회사명", key="corp_next")

    st.markdown("---")

    if st.button("이직 여부 계산하기"):
        if not current_industry or not target_industry:
            st.warning("직종을 모두 선택해 주세요.")
            return

        if salary <= 0:
            st.warning("연봉을 올바르게 입력해 주세요.")
            return

        if not corp_now or not corp_next:
            st.warning("현재 기업과 이직 기업을 모두 입력해 주세요.")
            return

        # 페이지 이동
        st.session_state.page = "result"
        st.rerun()


# ---------------- 페이지 2 (이직 여부 계산) ----------------
def page_result():
    st.title("이직 여부 결과")

    # 입력값 가져오기
    cur_ind = st.session_state.cur_ind
    tgt_ind = st.session_state.tgt_ind
    years = st.session_state.years
    salary = st.session_state.salary
    corp_now = st.session_state.corp_now
    corp_next = st.session_state.corp_next

    # DART API 데이터 조회
    now_metrics = fetch_corp_metrics(corp_now)
    next_metrics = fetch_corp_metrics(corp_next)

    g_now = get_industry_growth(cur_ind)
    g_next = get_industry_growth(tgt_ind)

    # 기본 성장 베이스
    salary_scale = salary * 10000 / 100_000_000  # 만원 → 원, 1억 기준
    SpBase = salary_scale * ((1 + g_now) ** years)

    factor_now = compute_company_factor(now_metrics, g_now)
    factor_next = compute_company_factor(next_metrics, g_next)

    # 최종 점수
    Wp = SpBase * factor_now
    Wk = SpBase * factor_next

    st.session_state.Wp = Wp
    st.session_state.Wk = Wk

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("현재 회사 Wp")
        st.markdown(f"<h2>{Wp:.2f}</h2>", unsafe_allow_html=True)

    with col2:
        st.subheader("결정 결과")
        if Wk > Wp:
            st.session_state.decision = "이직!"
        else:
            st.session_state.decision = "잔류!"

        st.markdown(f"<h1>{st.session_state.decision}</h1>", unsafe_allow_html=True)

    with col3:
        st.subheader("이직 고려 Wk")
        st.markdown(f"<h2>{Wk:.2f}</h2>", unsafe_allow_html=True)

    st.markdown("---")

    # 이직일 때만 연봉협상 메뉴로 이동 가능
    if st.session_state.decision == "이직!":
        if st.button("이직! (연봉 협상 페이지로 이동)"):
            st.session_state.page = "negotiation"
            st.rerun()
    else:
        st.button("이직! (연봉 협상 페이지로 이동)", disabled=True)

    if st.button("처음으로 돌아가기"):
        st.session_state.page = "input"
        st.rerun()


# ---------------- 페이지 3 (연봉 협상 메뉴) ----------------
def page_negotiation():
    st.title("피이직대학 이직 상담소 - 연봉 협상")

    st.markdown("### 2단계: 연봉 협상 시뮬레이션")

    current_salary = st.number_input("현재 연봉 (만원)", min_value=0, value=5000)
    ask_salary = st.number_input("희망 제시 연봉 (만원)", min_value=0, value=6000)

    st.markdown("---")

    if ask_salary <= current_salary:
        st.info("현재 연봉 이하로 제시할 필요가 없습니다.")
    elif ask_salary <= current_salary * 1.1:
        st.success("상대적으로 보수적인 제안입니다. 협상 성공 가능성이 높습니다.")
    elif ask_salary <= current_salary * 1.3:
        st.warning("꽤 공격적인 제안입니다. 근거를 잘 준비해야 합니다.")
    else:
        st.error("매우 공격적인 제안입니다. 협상 난항 가능성이 있습니다.")

    st.markdown("---")

    if st.button("이직 여부 결과 화면으로 돌아가기"):
        st.session_state.page = "result"
        st.rerun()


# ---------------- 라우팅 ----------------
def main():
    page = st.session_state.page

    if page == "input":
        page_input()
    elif page == "result":
        page_result()
    elif page == "negotiation":
        page_negotiation()
    else:
        st.session_state.page = "input"
        st.rerun()


if __name__ == "__main__":
    main()
