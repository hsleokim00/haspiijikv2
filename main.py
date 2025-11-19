import math
import requests
import streamlit as st

# ===================== 기본 설정 =====================
st.set_page_config(
    page_title="피이직대학 이직 상담소",
    page_icon="📈",
    layout="centered",
)

API_BASE = "https://black-bread-33be.dlspike520.workers.dev/"

# 산업별 평균 연봉 상승률 (HTML과 동일)
INDUSTRY_GROWTH = {
    "서비스업": 0.011,      # 1.1%
    "제조·화학업": 0.03,    # 3.0%
    "판매·유통업": 0.043,   # 4.3%
    "의료·제약업": 0.027,   # 2.7%
    "IT·통신업": 0.043      # 4.3%
}
INDUSTRY_OPTIONS = list(INDUSTRY_GROWTH.keys())

# ===================== 세션 상태 초기화 =====================
if "page" not in st.session_state:
    # p2: 이직 여부 결정, p3: 연봉협상 메뉴, p5: 연봉 협상 시뮬레이터, p4: 초기 연봉 제시
    st.session_state["page"] = "p2"

if "jc_result" not in st.session_state:
    st.session_state["jc_result"] = None


# ===================== 로직 함수들 =====================
def fetch_corp_metrics(name: str) -> dict:
    """
    Cloudflare Worker에서 회사 metrics 받아오기.
    HTML의 fetchCorpMetrics와 동일한 역할.
    """
    corp = name.strip()
    if not corp:
        raise ValueError("회사명이 비어 있습니다.")

    url = f"{API_BASE}?corp={requests.utils.quote(corp)}"
    res = requests.get(url, timeout=10)

    if not res.ok:
        raise RuntimeError(f"HTTP {res.status_code} 오류가 발생했습니다.")

    data = res.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "회사 데이터를 가져오지 못했습니다.")

    return data.get("metrics") or {}


def get_industry_growth(industry: str) -> float:
    """
    산업별 성장률 가져오기. 없는 경우 3% 기본값.
    """
    return INDUSTRY_GROWTH.get(industry, 0.03)


def company_factor(metrics: dict, industry_growth_fallback: float) -> float:
    """
    회사 지수 계산:
    - 매출 성장률(salesGrowth)을 우선 사용, 없으면 산업 성장률 사용
    - 자산(assets)을 log10으로 스케일링해서 규모 반영
    """
    sales_growth = metrics.get("salesGrowth")
    if isinstance(sales_growth, (int, float)):
        sg = float(sales_growth)
    else:
        sg = float(industry_growth_fallback)

    # 성장률 컴포넌트
    growth_component = 1.0 + sg

    # 자산(규모) 컴포넌트
    size_component = 1.0
    assets = metrics.get("assets")
    if isinstance(assets, (int, float)) and assets > 0:
        lg = math.log10(float(assets))
        size_component = lg / 12.0

    return growth_component * size_component


def format_score(x: float) -> str:
    """
    점수 포맷: 소수 둘째 자리까지 (HTML의 toFixed(2) 대응).
    """
    if not math.isfinite(x):
        return "-"
    return f"{x:.2f}"


def compute_job_change(
    years: float,
    salary: float,
    current_corp: str,
    next_corp: str,
    current_industry: str,
    target_industry: str,
):
    """
    HTML 2페이지(이직 여부 결정)에서 하던 Wp/Wk 계산.
    """
    if not current_industry or not target_industry:
        raise ValueError("현재 직종과 이직 고려 직종을 모두 선택해야 합니다.")
    if years < 0:
        raise ValueError("연차는 0 이상이어야 합니다.")
    if salary <= 0:
        raise ValueError("연봉은 0보다 커야 합니다.")
    if not current_corp.strip() or not next_corp.strip():
        raise ValueError("현재 기업과 이직 고려 기업명을 모두 입력해야 합니다.")

    # 회사 metrics 조회
    now_metrics = fetch_corp_metrics(current_corp)
    next_metrics = fetch_corp_metrics(next_corp)

    # 산업 성장률
    g_now_ind = get_industry_growth(current_industry)
    g_next_ind = get_industry_growth(target_industry)

    # SpBase 계산: (연봉 / 1억) × (1+산업성장률)^연차
    salary_scale = salary / 100_000_000  # 1억 기준
    sp_base = salary_scale * ((1.0 + g_now_ind) ** years)

    # 회사 계수
    factor_now = company_factor(now_metrics, g_now_ind)
    factor_next = company_factor(next_metrics, g_next_ind)

    # 최종 지수
    wp = sp_base * factor_now   # 현재 회사 Wp
    wk = sp_base * factor_next  # 이직 고려 Wk

    # 의사결정
    if math.isfinite(wp) and math.isfinite(wk):
        if wk > wp:
            decision = "이직!"
        elif wp > wk:
            decision = "잔류!"
        else:
            decision = "보류"
    else:
        decision = "계산 불가"

    return {
        "Wp": wp,
        "Wk": wk,
        "Wp_str": format_score(wp),
        "Wk_str": format_score(wk),
        "decision": decision,
        "now_metrics": now_metrics,
        "next_metrics": next_metrics,
        "g_now_ind": g_now_ind,
        "g_next_ind": g_next_ind,
        "sp_base": sp_base,
        "factor_now": factor_now,
        "factor_next": factor_next,
    }


# ===================== 공통 헤더 =====================
st.title("피이직대학 이직 상담소")

page = st.session_state["page"]
if page == "p2":
    st.subheader("- 이직 여부 결정")
elif page == "p3":
    st.subheader("- 연봉협상 메뉴")
elif page == "p5":
    st.subheader("- 연봉 협상 시뮬레이터")
elif page == "p4":
    st.subheader("- 초기 연봉 제시")


st.markdown("---")


# ===================== PAGE 2: 이직 여부 결정 =====================
if page == "p2":
    st.caption("연차, 연봉, 회사 규모·성장률을 기반으로 현재 회사(Wp)와 이직 회사(Wk)를 비교합니다.")

    with st.form("job_change_form"):
        st.markdown("#### 직종 정보")

        col1, col2 = st.columns(2)
        with col1:
            current_ind = st.selectbox(
                "현재 직종",
                INDUSTRY_OPTIONS,
                index=INDUSTRY_OPTIONS.index("IT·통신업") if "IT·통신업" in INDUSTRY_OPTIONS else 0,
            )
        with col2:
            target_ind = st.selectbox(
                "이직 고려 직종",
                INDUSTRY_OPTIONS,
                index=INDUSTRY_OPTIONS.index("IT·통신업") if "IT·통신업" in INDUSTRY_OPTIONS else 0,
            )

        st.markdown("#### 이직 여부 입력값")
        col3, col4 = st.columns(2)
        with col3:
            years = st.number_input(
                "연차 (년)",
                min_value=0.0,
                max_value=50.0,
                value=3.0,
                step=0.5,
            )
            current_corp = st.text_input("현재 기업", placeholder="예: 삼성전자")
        with col4:
            salary = st.number_input(
                "현재 연봉 (원)",
                min_value=1.0,
                max_value=5_000_000_000.0,
                value=50_000_000.0,
                step=1_000_000.0,
                format="%.0f",
            )
            next_corp = st.text_input("이직 기업", placeholder="예: 네이버")

        calc_submit = st.form_submit_button("계산")

    # 계산 버튼 눌렀을 때만 새로 계산
    if calc_submit:
        if not current_corp or not next_corp:
            st.error("현재 기업과 이직 기업을 모두 입력해 주세요.")
        else:
            try:
                res = compute_job_change(
                    years=years,
                    salary=salary,
                    current_corp=current_corp,
                    next_corp=next_corp,
                    current_industry=current_ind,
                    target_industry=target_ind,
                )
                st.session_state["jc_result"] = res
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

    result = st.session_state["jc_result"]

    # 결과 박스 (HTML p2의 3개 box를 비슷하게 구성)
    st.markdown("#### 이직 여부 결과")

    colA, colB, colC = st.columns(3)
    if result:
        with colA:
            st.markdown(
                f"""<div style="padding:16px;border-radius:12px;border:1px solid #ddd;text-align:center;">
                현재 회사 Wp<br><strong style="font-size:1.3rem;">{result['Wp_str']}</strong>
                </div>""",
                unsafe_allow_html=True,
            )
        with colB:
            # 가운데 '결과' 박스
            decision_text = result["decision"]
            st.markdown(
                f"""<div style="padding:16px;border-radius:12px;border:1px solid #ddd;
                text-align:center;font-size:1.4rem;font-weight:bold;">
                {decision_text}
                </div>""",
                unsafe_allow_html=True,
            )
        with colC:
            st.markdown(
                f"""<div style="padding:16px;border-radius:12px;border:1px solid #ddd;text-align:center;">
                이직 고려 Wk<br><strong style="font-size:1.3rem;">{result['Wk_str']}</strong>
                </div>""",
                unsafe_allow_html=True,
            )
    else:
        # 초기 상태
        with colA:
            st.markdown(
                """<div style="padding:16px;border-radius:12px;border:1px solid #ddd;text-align:center;">
                현재 회사 Wp<br><strong style="font-size:1.3rem;">-</strong>
                </div>""",
                unsafe_allow_html=True,
            )
        with colB:
            st.markdown(
                """<div style="padding:16px;border-radius:12px;border:1px solid #ddd;
                text-align:center;font-size:1.4rem;font-weight:bold;">
                결과
                </div>""",
                unsafe_allow_html=True,
            )
        with colC:
            st.markdown(
                """<div style="padding:16px;border-radius:12px;border:1px solid #ddd;text-align:center;">
                이직 고려 Wk<br><strong style="font-size:1.3rem;">-</strong>
                </div>""",
                unsafe_allow_html=True,
            )

    # 의사결정 및 추가 메시지 / 이직 버튼
    if result:
        decision = result["decision"]

        if decision == "잔류!":
            st.warning(
                "현재 회사의 Wp가 이직 회사의 Wk보다 높게 계산되었습니다.\n\n"
                "⚠️ 충분히 좋은 직장을 두고 왜 이직하시죠...?"
            )
        elif decision == "보류":
            st.info("두 회사의 지수가 거의 비슷합니다. 다른 요소(워라밸, 조직문화 등)를 더 고려해 보세요.")
        elif decision == "계산 불가":
            st.error("지수를 계산할 수 없습니다. 입력값과 회사 데이터를 다시 확인해 주세요.")

        # 🔴 여기서 중요한 부분: 이직! 버튼을 눌러야만 p3(연봉협상 메뉴)로 이동
        if decision == "이직!":
            st.success("이직 회사의 Wk가 현재 회사의 Wp보다 높게 계산되었습니다.")
            move = st.button("이직! (연봉 협상 메뉴로 이동)")
            if move:
                st.session_state["page"] = "p3"
                st.experimental_rerun()
        else:
            st.info("이직! 결과가 나와야 연봉협상 메뉴로 이동할 수 있습니다.")

    with st.expander("계산 상세 보기 (SpBase, 회사 계수 등)"):
        if result:
            st.write(f"연차: `{years}` 년")
            st.write(f"현재 직종 성장률 g_now_ind: `{result['g_now_ind']:.4f}`")
            st.write(f"이직 직종 성장률 g_next_ind: `{result['g_next_ind']:.4f}`")
            st.write(f"SpBase = (연봉 / 1억) × (1 + g_now_ind)^연차 = `{result['sp_base']:.4f}`")
            st.write(f"현재 회사 계수 factor_now: `{result['factor_now']:.4f}`")
            st.write(f"이직 회사 계수 factor_next: `{result['factor_next']:.4f}`")

            st.markdown("#### 현재 회사 metrics")
            st.json(result["now_metrics"])
            st.markdown("#### 이직 회사 metrics")
            st.json(result["next_metrics"])
        else:
            st.write("아직 계산된 결과가 없습니다.")

        st.markdown(
            """
            **공식 정리**

            - `SpBase = (연봉 / 100,000,000) × (1 + 산업성장률)^연차`
            - `Wp = SpBase × 회사계수(현재 회사)`
            - `Wk = SpBase × 회사계수(이직 회사)`
            - 회사계수:
                - 성장률 컴포넌트: `1 + salesGrowth` *(없으면 산업성장률 사용)*
                - 규모 컴포넌트: `log10(assets) / 12`
                - 최종: `(1 + 성장률) × (규모 컴포넌트)`
            """
        )


# ===================== PAGE 3: 연봉협상 메뉴 =====================
elif page == "p3":
    # 뒤로 버튼 (HTML top-bar의 '뒤로')
    if st.button("뒤로 (이직 여부 결정으로)", key="back_to_p2"):
        st.session_state["page"] = "p2"
        st.experimental_rerun()

    st.markdown("### 연봉협상 메뉴")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """<div style="padding:16px;border-radius:16px;border:1px solid #ddd;">
            <h3>연봉 협상 시뮬레이터</h3>
            <p>라운드별 협상 플로우는 이후 추가됩니다.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("들어가기", key="go_p5"):
            st.session_state["page"] = "p5"
            st.experimental_rerun()

    with col2:
        st.markdown(
            """<div style="padding:16px;border-radius:16px;border:1px solid #ddd;">
            <h3>초기 연봉 제시</h3>
            <p>초기 제시 연봉 계산은 이후 추가됩니다.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("들어가기", key="go_p4"):
            st.session_state["page"] = "p4"
            st.experimental_rerun()


# ===================== PAGE 5: 연봉 협상 시뮬레이터 (placeholder) =====================
elif page == "p5":
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p5"):
        st.session_state["page"] = "p3"
        st.experimental_rerun()

    st.markdown("### 연봉 협상 시뮬레이터")
    st.info("협상 라운드, 제안, 응답 등의 상세 UI는 여기 추가됩니다. (HTML p5 구조 그대로 반영)")


# ===================== PAGE 4: 초기 연봉 제시 (placeholder) =====================
elif page == "p4":
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p4"):
        st.session_state["page"] = "p3"
        st.experimental_rerun()

    st.markdown("### 초기 연봉 제시")
    st.info("초기 제시 연봉 계산 UI는 추후 완성됩니다. (HTML p4 구조 그대로 반영)")
