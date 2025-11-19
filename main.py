# app.py
import streamlit as st

# =========================
# 기본 설정
# =========================
st.set_page_config(
    page_title="피이직대학 이직 상담소",
    layout="wide",
)

# 세션 상태 초기화
if "step" not in st.session_state:
    st.session_state.step = 1  # 1: 이직 여부 입력, 2: 이직 여부 결과, 3: 연봉 협상
if "decision" not in st.session_state:
    st.session_state.decision = None
if "wp_current" not in st.session_state:
    st.session_state.wp_current = 0.5
if "wk_new" not in st.session_state:
    st.session_state.wk_new = 0.5

# =========================
# 공용 함수들
# =========================
def go_step(step: int):
    st.session_state.step = step


def calc_move_decision(wp_current: float, wk_new: float) -> str:
    """
    이직 여부 계산식 위치.

    ⚠️ 주의: 여기 부분에 원래 HTML/JS에서 쓰던 '그대로의 계산식'을 옮겨 넣으면 됨.
    지금은 임시로 "wk_new > wp_current 이면 이직, 아니면 잔류" 로 구현해 둠.
    """
    if wk_new > wp_current:
        return "이직"
    else:
        return "잔류"


def calc_salary_offer(
    current_salary: float,
    target_salary: float,
    worker_discount: float,
    firm_discount: float,
) -> float:
    """
    연봉협상(루빈스타인) 관련 계산식 위치.

    ⚠️ 여기도 나중에 네가 논문/모형에서 썼던 정확한 수식을 그대로 넣으면 됨.
    지금은 '현재 연봉과 목표 연봉 사이에서 할인율을 반영한 절충안' 정도로
    예시 공식을 넣어둠.

    예시:
        worker_share = (1 - firm_discount) / (1 - worker_discount * firm_discount)
        제안 연봉 = 현재연봉 + worker_share * (목표연봉 - 현재연봉)
    """
    if worker_discount <= 0 or worker_discount >= 1:
        worker_discount = 0.9
    if firm_discount <= 0 or firm_discount >= 1:
        firm_discount = 0.9

    worker_share = (1.0 - firm_discount) / (1.0 - worker_discount * firm_discount)
    worker_share = max(0.0, min(1.0, worker_share))

    offer = current_salary + worker_share * (target_salary - current_salary)
    return offer


def inject_css():
    """UI 조금 다듬는 CSS (Streamlit 기본 디자인 위에 살짝 입히기)."""
    st.markdown(
        """
        <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
                         "Noto Sans KR", system-ui, sans-serif;
        }
        .main {
            padding-top: 2rem;
        }
        .big-title {
            font-size: 40px;
            font-weight: 800;
            margin-bottom: 8px;
        }
        .subtitle {
            font-size: 18px;
            opacity: 0.85;
            margin-bottom: 32px;
        }
        .card {
            background: #ffffff;
            border-radius: 18px;
            padding: 24px 26px;
            box-shadow: 0 8px 20px rgba(0,0,0,0.06);
            margin-bottom: 24px;
        }
        .decision-box {
            text-align: center;
            padding: 40px 20px;
            border-radius: 20px;
            background: linear-gradient(135deg, #ff6b6b, #feca57);
            color: #ffffff;
            font-size: 34px;
            font-weight: 900;
        }
        .decision-box.stay {
            background: linear-gradient(135deg, #4b7bec, #a55eea);
        }
        .small-label {
            font-size: 14px;
            opacity: 0.8;
            margin-bottom: 4px;
        }
        .section-title {
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 10px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =========================
# 각 스텝 화면들
# =========================
def render_header(subtitle: str):
    st.markdown('<div class="big-title">피이직대학 이직 상담소</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="subtitle">{subtitle}</div>', unsafe_allow_html=True)


def render_step1():
    # 1단계: 현재 회사 / 이직 고려 회사 정보 입력
    render_header("1단계: 현재 회사와 이직 고려 회사의 정보를 입력하세요.")

    with st.container():
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">현재 회사 정보</div>', unsafe_allow_html=True)
        st.markdown('<div class="small-label">현재 회사 Wp (워크플레이스 지수)</div>', unsafe_allow_html=True)

        wp_current = st.slider(
            "현재 회사 Wp (워크플레이스 지수)",
            min_value=0.0,
            max_value=1.0,
            value=float(st.session_state.wp_current),
            step=0.01,
            label_visibility="collapsed",
        )
        st.session_state.wp_current = wp_current
        st.markdown("</div>", unsafe_allow_html=True)

    with st.container():
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">이직 고려 회사 정보</div>', unsafe_allow_html=True)
        st.markdown('<div class="small-label">이직 고려 Wk (워크플레이스 지수)</div>', unsafe_allow_html=True)

        wk_new = st.slider(
            "이직 고려 Wk (워크플레이스 지수)",
            min_value=0.0,
            max_value=1.0,
            value=float(st.session_state.wk_new),
            step=0.01,
            label_visibility="collapsed",
        )
        st.session_state.wk_new = wk_new
        st.markdown("</div>", unsafe_allow_html=True)

    st.info("※ 기존 HTML 파일의 실제 계산식이 있다면, 이 앱에서는 wp_current, wk_new 변수를 그대로 사용하면 됩니다.")

    col1, col2 = st.columns([2, 1])
    with col1:
        if st.button("이직 여부 계산하기", type="primary", use_container_width=True):
            decision = calc_move_decision(st.session_state.wp_current, st.session_state.wk_new)
            st.session_state.decision = decision
            go_step(2)
            st.experimental_rerun()


def render_step2():
    # 2단계: 이직 여부 결과
    render_header("2단계: 이직 여부를 확인해 보세요.")

    st.button("← 1단계로 돌아가기", on_click=lambda: go_step(1))

    decision = st.session_state.decision
    if decision is None:
        st.warning("아직 이직 여부를 계산하지 않았어요. 1단계에서 먼저 계산을 해 주세요.")
        return

    diff = st.session_state.wk_new - st.session_state.wp_current

    if decision == "이직":
        box_class = "decision-box"
        title_text = "이직!"
        sub_text = "이직 고려 회사의 워크플레이스 지수가 더 높게 나왔어요."
    else:
        box_class = "decision-box stay"
        title_text = "잔류!"
        sub_text = "현재 회사에 남는 것이 더 나은 선택으로 계산되었어요."

    st.markdown(
        f"""
        <div class="card">
            <div class="{box_class}">
                {title_text}
            </div>
            <div style="margin-top: 24px; font-size: 16px;">
                <b>현재 회사 Wp:</b> {st.session_state.wp_current:.2f} &nbsp;&nbsp;|&nbsp;&nbsp;
                <b>이직 고려 Wk:</b> {st.session_state.wk_new:.2f} &nbsp;&nbsp;|&nbsp;&nbsp;
                <b>차이(Wk - Wp):</b> {diff:+.2f}
            </div>
            <div style="margin-top: 12px; font-size: 14px; opacity: 0.8;">
                ※ 실제 연구에서 사용한 상세 계산식은 calc_move_decision() 함수 안에 그대로 옮겨 넣어 사용할 수 있습니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if decision == "이직":
        st.success("이직을 선택한 상황이므로, 아래 버튼을 눌러 연봉 협상 단계로 넘어갈 수 있습니다.")
        if st.button("이직!  → 연봉 협상 단계로 이동", type="primary", use_container_width=True):
            go_step(3)
            st.experimental_rerun()
    else:
        st.info("잔류로 계산된 상태에서는 기본적으로 연봉협상 단계로 넘어가지 않도록 막아두었습니다.\n"
                "연구/시뮬레이션용으로 연봉 협상을 테스트하고 싶다면 아래 버튼을 눌러 강제로 이동할 수 있어요.")
        if st.button("그래도 연봉 협상 화면 한 번 보기"):
            go_step(3)
            st.experimental_rerun()


def render_step3():
    # 3단계: 연봉 협상
    render_header("3단계: 이직 시 연봉 협상 시뮬레이션")

    cols = st.columns([1, 1])
    with cols[0]:
        st.button("← 이직 여부 페이지로", on_click=lambda: go_step(2))

    with st.container():
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">기본 정보 입력</div>', unsafe_allow_html=True)

        current_salary = st.number_input(
            "현재 회사에서의 연봉 (만원 단위)",
            min_value=0.0,
            value=5000.0,
            step=100.0,
        )
        target_salary = st.number_input(
            "이직 시 목표 연봉 (만원 단위)",
            min_value=0.0,
            value=7000.0,
            step=100.0,
        )

        st.markdown("</div>", unsafe_allow_html=True)

    with st.container():
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">협상 시 할인율 (현재 가치 반영)</div>', unsafe_allow_html=True)

        st.markdown(
            "노동자와 회사의 **할인율(0~1)** 은 시간이 지날수록 가치가 얼마나 감소하는지를 나타내는 값으로, "
            "1에 가까울수록 '기다리는 데 덜 조급함'을 의미해요."
        )

        col1, col2 = st.columns(2)
        with col1:
            worker_discount = st.slider(
                "노동자 할인율 (δ_worker)",
                min_value=0.0,
                max_value=0.99,
                value=0.9,
                step=0.01,
            )
        with col2:
            firm_discount = st.slider(
                "회사 할인율 (δ_firm)",
                min_value=0.0,
                max_value=0.99,
                value=0.9,
                step=0.01,
            )

        st.markdown("</div>", unsafe_allow_html=True)

    if st.button("연봉 협상 결과 계산하기", type="primary", use_container_width=True):
        offer = calc_salary_offer(
            current_salary=current_salary,
            target_salary=target_salary,
            worker_discount=worker_discount,
            firm_discount=firm_discount,
        )

        st.markdown(
            f"""
            <div class="card">
                <div class="section-title">협상 결과 (예시 모형)</div>
                <p>
                    연구에서 사용한 루빈스타인 모형의 구체적인 식을 그대로 반영하려면
                    <code>calc_salary_offer()</code> 함수를 수정하면 됩니다.<br/>
                    아래 값은 현재 연봉, 목표 연봉, 할인율을 반영한 <b>예시 제안 연봉</b>이에요.
                </p>
                <ul>
                    <li><b>현재 연봉:</b> {current_salary:,.0f} 만원</li>
                    <li><b>목표 연봉:</b> {target_salary:,.0f} 만원</li>
                    <li><b>노동자 할인율 δ_worker:</b> {worker_discount:.2f}</li>
                    <li><b>회사 할인율 δ_firm:</b> {firm_discount:.2f}</li>
                </ul>
                <hr/>
                <p style="font-size: 20px; font-weight: 700;">
                    ▶ 예시 제안 연봉: {offer:,.0f} 만원
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )


# =========================
# 메인 실행부
# =========================
def main():
    inject_css()

    step = st.session_state.step

    if step == 1:
        render_step1()
    elif step == 2:
        render_step2()
    elif step == 3:
        render_step3()
    else:
        go_step(1)
        render_step1()


if __name__ == "__main__":
    main()
