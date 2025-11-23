import math
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

import streamlit as st

# ===================== 기본 설정 =====================
st.set_page_config(
    page_title="피이직대학 이직 상담소",
    page_icon="📈",
    layout="centered",
)

# ----------------- 직종별 최대 연봉 E_max (예시) -----------------
DEFAULT_E_BY_FIELD: Dict[str, float] = {
    "it_dev": 9000.0,
    "medical": 12000.0,
    "driver": 6000.0,
    "service": 5000.0,
    "manufacturing": 7000.0,
}

FIELD_LABELS: Dict[str, str] = {
    "it_dev": "IT·개발",
    "medical": "의료",
    "driver": "운전·운송·배송",
    "service": "서비스",
    "manufacturing": "제조·생산",
}

FIRST_MOVER_LABELS: Dict[str, str] = {
    "employee": "구직자(employee)",
    "employer": "회사(employer)",
}

FirstMover = Literal["employee", "employer"]


# ===================== 세션 상태 초기화 =====================

def init_global_state():
    if "page" not in st.session_state:
        st.session_state.page = "input"  # 'input', 'decision', 'negotiation'

    if "decision" not in st.session_state:
        st.session_state.decision = None  # 'move' or 'stay'

    if "current_salary" not in st.session_state:
        st.session_state.current_salary = 0.0
    if "offer_salary" not in st.session_state:
        st.session_state.offer_salary = 0.0
    if "Wp_current" not in st.session_state:
        st.session_state.Wp_current = 5.0
    if "Wk_new" not in st.session_state:
        st.session_state.Wk_new = 5.0

    # 협상 시뮬레이터용
    if "first_mover_choice" not in st.session_state:
        st.session_state.first_mover_choice = None  # "employee"/"employer"
    if "neg_total_rounds" not in st.session_state:
        st.session_state.neg_total_rounds = None
    if "neg_config" not in st.session_state:
        st.session_state.neg_config = None
    if "neg_session" not in st.session_state:
        st.session_state.neg_session = None


# ===================== 이직 여부 결정 로직 (간단 버전) =====================

def compute_job_change_decision(
    current_salary: float,
    offer_salary: float,
    Wp_current: float,
    Wk_new: float,
    alpha_salary: float = 0.7,
    alpha_work: float = 0.3,
) -> str:
    """
    간단한 효용 함수 예시:
    U = α * log(연봉) + (1-α) * 워크플레이스 지수

    반환값: 'move' (이직) 또는 'stay' (잔류)
    """
    # 로그 계산용 보호
    c_sal = max(current_salary, 1.0)
    o_sal = max(offer_salary, 1.0)

    U_current = alpha_salary * math.log(c_sal) + alpha_work * Wp_current
    U_new = alpha_salary * math.log(o_sal) + alpha_work * Wk_new

    return "move" if U_new > U_current else "stay"


# ===================== 협상 시뮬레이터 데이터 구조 =====================

@dataclass
class NegotiationConfig:
    """협상 기본 설정 값"""

    target_salary_S: float  # 목표 최종 연봉 S
    min_accept_B: float     # 최소 수용 연봉 B
    field_key: str          # 직종 키 (E_max 테이블용)
    first_mover: FirstMover # 첫 제안자
    total_rounds: int       # 전체 라운드 수
    delta_E: float          # 구직자 할인율
    delta_R: float          # 회사 할인율

    def e_max(self) -> float:
        return DEFAULT_E_BY_FIELD.get(self.field_key, self.target_salary_S)


@dataclass
class OfferHistoryEntry:
    round_index: int
    proposer: FirstMover
    offer_salary: float
    accepted: bool = False
    note: str = ""


@dataclass
class NegotiationSession:
    config: NegotiationConfig
    history: List[OfferHistoryEntry] = field(default_factory=list)
    finished: bool = False
    final_salary: Optional[float] = None

    def add_offer(
        self,
        round_index: int,
        proposer: FirstMover,
        offer_salary: float,
        accepted: bool,
        note: str = "",
    ):
        self.history.append(
            OfferHistoryEntry(
                round_index=round_index,
                proposer=proposer,
                offer_salary=offer_salary,
                accepted=accepted,
                note=note,
            )
        )
        if accepted:
            self.finished = True
            self.final_salary = offer_salary


# ===================== 협상 로직 (예시) =====================

def compute_equilibrium_offer(
    config: NegotiationConfig,
    round_index: int,
    proposer: FirstMover,
) -> float:
    """
    라운드, 제안자에 따라 제안 연봉을 계산하는 예시 함수.
    (실제 연구에서 쓰는 정식이 있으면 여기만 교체하면 됨.)
    """
    S = config.target_salary_S
    B = config.min_accept_B
    E_max = config.e_max()
    dE = config.delta_E
    dR = config.delta_R

    t = round_index / max(1, config.total_rounds - 1)

    if proposer == "employee":
        # 구직자는 S에서 B로 내려오는 방향
        offer = S - t * (S - B) * dE
    else:
        # 회사는 B에서 S로 올라가는 방향
        offer = B + t * (S - B) * dR

    offer = min(offer, E_max)
    return round(offer, 1)


def simulate_session(config: NegotiationConfig) -> NegotiationSession:
    """
    간단 시뮬레이션:
    - 매 라운드 한 번 제안
    - B 이상이면 수락, 아니면 다음 라운드
    """
    session = NegotiationSession(config=config)
    current_proposer: FirstMover = config.first_mover

    for r in range(1, config.total_rounds + 1):
        offer = compute_equilibrium_offer(config, r - 1, current_proposer)

        accepted = offer >= config.min_accept_B or r == config.total_rounds
        note = "수락" if accepted else "거절"

        session.add_offer(
            round_index=r,
            proposer=current_proposer,
            offer_salary=offer,
            accepted=accepted,
            note=note,
        )

        if session.finished:
            break

        current_proposer = "employer" if current_proposer == "employee" else "employee"

    return session


# ===================== 페이지 1: 입력 =====================

def page_input():
    st.title("피이직대학 이직 상담소")
    st.subheader("1단계: 이직 여부 결정 입력")

    st.markdown("현재 회사와 이직 고려 회사의 정보를 입력하세요.")

    col1, col2 = st.columns(2)

    with col1:
        current_salary = st.number_input(
            "현재 연봉 (만원)",
            min_value=0.0,
            max_value=50000.0,
            value=5000.0,
            step=100.0,
        )
        Wp_current = st.slider(
            "현재 회사 워크플레이스 지수 (Wp)",
            min_value=0.0,
            max_value=10.0,
            value=6.0,
            step=0.1,
        )

    with col2:
        offer_salary = st.number_input(
            "이직 시 제안/목표 연봉 (만원)",
            min_value=0.0,
            max_value=50000.0,
            value=7000.0,
            step=100.0,
        )
        Wk_new = st.slider(
            "이직 고려 회사 워크플레이스 지수 (Wk)",
            min_value=0.0,
            max_value=10.0,
            value=7.5,
            step=0.1,
        )

    st.markdown("---")

    if st.button("이직 여부 계산"):
        # 값 저장
        st.session_state.current_salary = current_salary
        st.session_state.offer_salary = offer_salary
        st.session_state.Wp_current = Wp_current
        st.session_state.Wk_new = Wk_new

        decision = compute_job_change_decision(
            current_salary=current_salary,
            offer_salary=offer_salary,
            Wp_current=Wp_current,
            Wk_new=Wk_new,
        )
        st.session_state.decision = decision
        st.session_state.page = "decision"

    st.info("※ 위 정보를 입력한 뒤 **이직 여부 계산** 버튼을 눌러 주세요.")


# ===================== 페이지 2: 이직 여부 결과 =====================

def page_decision():
    st.title("피이직대학 이직 상담소")
    st.subheader("2단계: 이직 여부 결과")

    if st.session_state.decision is None:
        st.warning("먼저 1단계에서 값을 입력하고 이직 여부를 계산해 주세요.")
        if st.button("1단계로 돌아가기"):
            st.session_state.page = "input"
        return

    cur_sal = st.session_state.current_salary
    off_sal = st.session_state.offer_salary
    Wp = st.session_state.Wp_current
    Wk = st.session_state.Wk_new

    st.markdown("#### 입력 요약")
    col1, col2 = st.columns(2)
    with col1:
        st.write(f"- 현재 연봉: **{cur_sal:,.0f} 만원**")
        st.write(f"- 현재 Wp: **{Wp:.1f}**")
    with col2:
        st.write(f"- 이직 연봉: **{off_sal:,.0f} 만원**")
        st.write(f"- 이직 Wk: **{Wk:.1f}**")

    st.markdown("---")

    if st.session_state.decision == "move":
        st.success("모델 기준으로 **이직!** 이 더 유리한 선택입니다.")
        st.markdown(
            """
            ### 🔵 이직!
            이직이 더 높은 효용(연봉 + 워크플레이스 지수)을 가지는 것으로 계산되었습니다.
            연봉 협상 시뮬레이터로 넘어가서, 구체적인 협상 과정을 살펴볼 수 있습니다.
            """
        )

        # 👉 반드시 이 버튼을 눌러야 협상 페이지로 넘어가게
        if st.button("이직! 연봉 협상 시뮬레이터로 이동"):
            st.session_state.page = "negotiation"
    else:
        st.info("모델 기준으로는 **잔류**가 더 나은 선택으로 계산되었습니다.")
        st.markdown(
            """
            ### 🟠 잔류
            현재 회사에 남는 것이 효용 측면에서 더 우세한 것으로 계산되었습니다.
            """
        )

    st.markdown("---")
    if st.button("1단계로 돌아가 다시 입력하기"):
        st.session_state.page = "input"


# ===================== 페이지 3: 협상 시뮬레이터 =====================

def page_negotiation():
    st.title("피이직대학 이직 상담소")
    st.subheader("3단계: 협상 시뮬레이터")

    st.caption("※ 먼저 첫 제안자를 선택하면, 기본 라운드 수가 자동 설정됩니다.")

    # --- 1. 첫 제안자 드롭다운만 먼저 노출 ---
    label_to_key = {
        "구직자(employee)": "employee",
        "회사(employer)": "employer",
    }
    labels = list(label_to_key.keys())

    default_index = 0
    if st.session_state.first_mover_choice is not None:
        for i, lb in enumerate(labels):
            if label_to_key[lb] == st.session_state.first_mover_choice:
                default_index = i
                break

    selected_label = st.selectbox(
        "첫 제안자를 선택하세요.",
        labels,
        index=default_index,
        help="처음 협상 제안을 누가 할지 선택하면, 기본 라운드 수(3 또는 4)가 자동으로 설정됩니다.",
    )
    selected_key = label_to_key[selected_label]

    # 선택이 바뀌면 라운드 수 자동 설정 + 세션 리셋
    if st.session_state.first_mover_choice != selected_key:
        st.session_state.first_mover_choice = selected_key
        if selected_key == "employer":
            st.session_state.neg_total_rounds = 4
        else:
            st.session_state.neg_total_rounds = 3
        st.session_state.neg_session = None
        st.session_state.neg_config = None

    # 첫 제안자가 없는 경우 방어 (실질적으로는 발생 X)
    if st.session_state.first_mover_choice is None:
        st.info("먼저 첫 제안자를 선택해 주세요.")
        return

    st.markdown("---")
    st.markdown("### 협상 기본 설정")

    col1, col2 = st.columns(2)

    # 이직 단계에서 쓰던 값들 기본값으로 사용
    default_target = max(st.session_state.offer_salary, 1000.0)
    default_min = max(st.session_state.offer_salary * 0.7, 1000.0)

    with col1:
        target_S = st.number_input(
            "목표 최종 연봉 S (만원 단위 예: 7000 → 7,000만원)",
            min_value=0.0,
            max_value=50000.0,
            value=default_target,
            step=100.0,
        )
        min_B = st.number_input(
            "최소 수용 연봉 B (만원)",
            min_value=0.0,
            max_value=50000.0,
            value=default_min,
            step=100.0,
        )
        total_rounds = st.number_input(
            "전체 라운드 수 (왕복 교대 제안 횟수)",
            min_value=1,
            max_value=20,
            value=st.session_state.neg_total_rounds or 4,
            step=1,
            help="첫 제안자 선택 시 3 또는 4로 자동 설정됩니다. 필요하면 여기서 수정해도 됩니다.",
        )
        st.session_state.neg_total_rounds = total_rounds

    with col2:
        field_key = st.selectbox(
            "직종 (E_max 테이블 키)",
            options=list(FIELD_LABELS.keys()),
            format_func=lambda k: FIELD_LABELS.get(k, k),
        )

        st.text_input(
            "첫 제안자",
            value=FIRST_MOVER_LABELS[st.session_state.first_mover_choice],
            disabled=True,
        )

        delta_E = st.slider(
            "초기 구직자 할인율 δ_E",
            min_value=0.50,
            max_value=0.99,
            value=0.95,
            step=0.01,
        )
        delta_R = st.slider(
            "초기 회사 할인율 δ_R",
            min_value=0.50,
            max_value=0.99,
            value=0.95,
            step=0.01,
        )

    if st.button("새 협상 세션 시작"):
        config = NegotiationConfig(
            target_salary_S=target_S,
            min_accept_B=min_B,
            field_key=field_key,
            first_mover=st.session_state.first_mover_choice,  # "employee"/"employer"
            total_rounds=st.session_state.neg_total_rounds,
            delta_E=delta_E,
            delta_R=delta_R,
        )
        session = simulate_session(config)
        st.session_state.neg_config = config
        st.session_state.neg_session = session

    st.markdown("---")
    st.markdown("### 협상 결과")

    session: Optional[NegotiationSession] = st.session_state.neg_session
    config: Optional[NegotiationConfig] = st.session_state.neg_config

    if session is None or config is None:
        st.info("위에서 협상 기본 설정을 마친 뒤 **새 협상 세션 시작** 버튼을 눌러 주세요.")
    else:
        if session.final_salary is not None:
            st.success(
                f"최종 합의 연봉: **{session.final_salary:,.1f} 만원**  "
                f"(총 {len(session.history)}라운드에서 합의)"
            )
        else:
            st.warning(
                f"{config.total_rounds}라운드 이내에 합의가 이루어지지 않았습니다.  \n"
                f"(마지막 제안: {session.history[-1].offer_salary:,.1f} 만원)"
            )

        st.markdown("#### 라운드별 제안 히스토리")

        for entry in session.history:
            with st.expander(f"라운드 {entry.round_index}"):
                who = "구직자" if entry.proposer == "employee" else "회사"
                st.write(f"- 제안자: **{who}**")
                st.write(f"- 제안 연봉: **{entry.offer_salary:,.1f} 만원**")
                st.write(f"- 결과: {'✅ 수락' if entry.accepted else '❌ 거절'}")
                if entry.note:
                    st.write(f"- 메모: {entry.note}")

    st.markdown("---")
    if st.button("2단계 결과 화면으로 돌아가기"):
        st.session_state.page = "decision"


# ===================== main =====================

def main():
    init_global_state()

    if st.session_state.page == "input":
        page_input()
    elif st.session_state.page == "decision":
        page_decision()
    elif st.session_state.page == "negotiation":
        page_negotiation()
    else:
        # 예상치 못한 값 방어
        st.session_state.page = "input"
        page_input()


if __name__ == "__main__":
    main()
