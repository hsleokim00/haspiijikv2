import streamlit as st

# ---------------- 기본 설정 ----------------
st.set_page_config(
    page_title="피이직대학 이직 상담소",
    page_icon="💼",
    layout="wide",
)

# ---------------- 상태 초기화 ----------------
if "page" not in st.session_state:
    st.session_state.page = "input"  # 첫 화면
if "decision" not in st.session_state:
    st.session_state.decision = None
if "Wp_current" not in st.session_state:
    st.session_state.Wp_current = 0.0
if "Wk_new" not in st.session_state:
    st.session_state.Wk_new = 0.0


# ---------------- 각 페이지 함수 ----------------
def page_input():
    st.title("피이직대학 이직 상담소")
    st.subheader("1단계: 현재 회사와 이직 고려 회사의 정보를 입력하세요.")

    st.markdown("##### 현재 회사 정보")
    wp_current = st.slider(
        "현재 회사 Wp (워크플레이스 지수)",
        min_value=0.0,
        max_value=1.0,
        value=0.49,
        step=0.01,
    )

    st.markdown("---")
    st.markdown("##### 이직 고려 회사 정보")
    wk_new = st.slider(
        "이직 고려 Wk (워크플레이스 지수)",
        min_value=0.0,
        max_value=1.0,
        value=0.59,
        step=0.01,
    )

    st.info("※ 실제 계산식이 있다면 여기에서 wp_current, wk_new를 이용해 계산식을 넣으면 됩니다.")

    if st.button("이직 여부 계산하기"):
        st.session_state.Wp_current = wp_current
        st.session_state.Wk_new = wk_new

        # 간단한 판별 로직 (Wk_new > Wp_current 이면 이직 권장)
        if wk_new > wp_current:
            st.session_state.decision = "이직"
        else:
            st.session_state.decision = "잔류"

        st.session_state.page = "result"
        st.rerun()


def page_result():
    st.title("이직 여부 결과")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### 현재 회사 Wp")
        st.markdown(f"<h1 style='text-align:center;'>{st.session_state.Wp_current:.2f}</h1>",
                    unsafe_allow_html=True)

    with col2:
        st.markdown("### 결정 결과")
        if st.session_state.decision == "이직":
            text = "이직!"
        elif st.session_state.decision == "잔류":
            text = "잔류!"
        else:
            text = "미결정"
        st.markdown(
            f"<h1 style='text-align:center;'>{text}</h1>",
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown("### 이직 고려 Wk")
        st.markdown(f"<h1 style='text-align:center;'>{st.session_state.Wk_new:.2f}</h1>",
                    unsafe_allow_html=True)

    st.markdown("---")

    if st.session_state.decision == "이직":
        st.success("이직 회사의 Wk가 현재 회사의 Wp보다 높게 계산되었습니다.")
    elif st.session_state.decision == "잔류":
        st.warning("현재 회사의 Wp가 이직 고려 회사의 Wk보다 높거나 비슷하게 계산되었습니다.")
    else:
        st.info("위 입력값을 이용해 먼저 이직 여부를 계산해주세요.")

    st.markdown("")

    # 👉 반드시 이 버튼을 눌러야 연봉 협상 페이지로 이동하도록 설정
    if st.session_state.decision == "이직":
        if st.button("이직! (연봉 협상 메뉴로 이동)"):
            st.session_state.page = "negotiation"
            st.rerun()
    else:
        st.button("이직! (연봉 협상 메뉴로 이동)", disabled=True)

    # 뒤로 가기
    if st.button("입력 화면으로 돌아가기"):
        st.session_state.page = "input"
        st.rerun()


def page_negotiation():
    st.title("피이직대학 이직 상담소 - 연봉 협상")

    st.markdown("### 2단계: 연봉 협상 시뮬레이션")

    st.write(
        """
        여기에는 네가 HTML 버전에서 만들었던 연봉 협상 UI를
        그대로 옮겨오거나, 새로운 슬라이더/입력창/그래프 등을 넣으면 돼.
        예시는 아주 간단한 버전으로만 만들어 둘게.
        """
    )

    current_salary = st.number_input("현재 연봉 (만원)", min_value=0, value=5000, step=100)
    ask_salary = st.number_input("희망 제시 연봉 (만원)", min_value=0, value=6000, step=100)

    st.markdown("---")
    st.write("#### 단순 협상 결과 예시")

    if ask_salary <= current_salary:
        st.info("현재 연봉 이하로는 제시할 필요가 없어요. 다시 한 번 생각해 봅시다.")
    elif ask_salary <= current_salary * 1.1:
        st.success("상대적으로 보수적인 제안입니다. 협상 성공 가능성이 높아요.")
    elif ask_salary <= current_salary * 1.3:
        st.warning("공격적인 제안입니다. 근거(성과, 시장가 등)를 잘 준비하세요.")
    else:
        st.error("매우 공격적인 제안입니다. 협상 결렬 가능성도 염두에 두세요.")

    if st.button("이직 여부 결과 화면으로 돌아가기"):
        st.session_state.page = "result"
        st.rerun()


# ---------------- 라우터 ----------------
def main():
    page = st.session_state.page

    if page == "input":
        page_input()
    elif page == "result":
        page_result()
    elif page == "negotiation":
        page_negotiation()
    else:
        # 혹시 모를 예외 상황
        st.session_state.page = "input"
        st.rerun()


if __name__ == "__main__":
    main()
