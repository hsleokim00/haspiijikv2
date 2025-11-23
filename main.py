import math
import requests
import streamlit as st
from dataclasses import dataclass, field
from typing import Literal, List, Dict, Optional

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

# ===================== NegotiationModel 정의 =====================

# 직종별 고용주 최대 지불 의사 연봉 E_max (예시용; 페이지 4에서는 직접 숫자로 넣어서 사용)
DEFAULT_E_BY_FIELD: Dict[str, float] = {
    "it_dev": 9000.0,
    "medical": 12000.0,
    "driver": 6000.0,
    "service": 5000.0,
    "manufacturing": 7000.0,
}

# 🔗 이직 여부 결정(Wk)에서 선택한 직종 → 연봉 협상 직종(E_max 키) 매핑
INDUSTRY_TO_FIELD: Dict[str, str] = {
    "서비스업": "service",
    "제조·화학업": "manufacturing",
    "판매·유통업": "service",   # 필요하면 나중에 다른 키로 바꿔도 됨
    "의료·제약업": "medical",
    "IT·통신업": "it_dev",
}

# 직종 키 → 한국어 라벨 (UI용)
FIELD_LABELS: Dict[str, str] = {
    "it_dev": "IT·개발",
    "medical": "의료",
    "driver": "운전·운송·배송",
    "service": "서비스",
    "manufacturing": "생산·제조",
}


@dataclass
class NegotiationState:
    # 고정 파라미터
    S_target: float          # 목표 최종 연봉 S
    B: float                 # 최소 허용 연봉 B
    E_max: float             # 고용주 최대 연봉 E (field에서 가져옴 / 커스텀 테이블)
    field_name: str          # 직종 이름(키)
    first_mover: str         # 'employee' or 'employer'
    total_rounds: int        # 전체 라운드 수

    # 할인율 (업데이트 가능)
    delta_E: float = 0.95    # 구직자 할인율
    delta_R: float = 0.95    # 고용주 할인율
    delta_E_hat: float = 0.95  # 고용주가 추정하는 구직자 할인율

    # 진행 중 상태
    current_round: int = 1
    history_employee: List[float] = field(default_factory=list)
    history_employer: List[float] = field(default_factory=list)

    def remaining_rounds(self) -> int:
        """현재 라운드를 포함해 앞으로 남은 전체 라운드 수입니다."""
        return max(self.total_rounds - self.current_round + 1, 0)

    @property
    def pi(self) -> float:
        """협상의 파이 크기 π = E_max - B 입니다."""
        return self.E_max - self.B

    def target_share(self) -> float:
        """
        파이에서 구직자가 가져가고 싶은 비율 x = (S - B)/π입니다.
        x ∈ [0,1] 범위인지 체크하여, 목표 연봉이 협상 구간 안인지 확인합니다.
        """
        if self.pi <= 0:
            raise ValueError("E_max는 B보다 커야 합니다.")
        return (self.S_target - self.B) / self.pi


class NegotiationModel:
    """
    실시간 연봉 협상 모델입니다.
    - 상태(state)를 들고 있다가
    - employer 오퍼가 들어오면 할인율 등을 업데이트하고
    - employee 차례가 되면 '지금 얼마를 제안해야 하는지'를 계산해서 돌려줍니다.
    """

    def __init__(
        self,
        S: float,
        B: float,
        field_name: str,
        first_mover: str,
        total_rounds: int = 4,
        E_table: Optional[Dict[str, float]] = None,
        delta_E_default: float = 0.95,
        delta_R_default: float = 0.95,
    ) -> None:
        first_mover = first_mover.lower()
        if first_mover not in ("employee", "employer"):
            raise ValueError("first_mover는 'employee' 또는 'employer'여야 합니다.")

        if E_table is None:
            E_table = DEFAULT_E_BY_FIELD
        if field_name not in E_table:
            raise KeyError(
                f"Unknown field '{field_name}'. "
                f"Add it to E_table or pass a custom E_table."
            )

        E_max = E_table[field_name]
        state = NegotiationState(
            S_target=S,
            B=B,
            E_max=E_max,
            field_name=field_name,
            first_mover=first_mover,
            total_rounds=total_rounds,
            delta_E=delta_E_default,
            delta_R=delta_R_default,
            delta_E_hat=delta_E_default,
        )

        # 타겟 비율이 0~1 안에 있는지 체크
        x = state.target_share()
        if not (0.0 <= x <= 1.0):
            raise ValueError(
                f"S_target={S}는 유효한 범위 "
                f"[B={B}, E_max={E_max}] (x={x:.3f}) 밖에 있습니다."
            )

        self.state = state

        # 🔹 라운드별 할인율 로그 저장용
        self.delta_history: List[Dict[str, float]] = []

    # 1) 고용주 오퍼 관찰 -> 상태 & 할인율 업데이트
    def observe_employer_offer(self, offer: float) -> None:
        """
        고용주가 새 오퍼를 했을 때 호출합니다.
        - 히스토리에 기록
        - 델타_R, delta_E_hat 갱신 (휴리스틱)
        """
        s = self.state
        s.history_employer.append(offer)

        # B~S 사이에서 현재 오퍼가 어디쯤인지
        denom = max(s.S_target - s.B, 1e-9)
        ratio_to_target = (offer - s.B) / denom
        ratio_to_target = max(0.0, min(ratio_to_target, 1.5))

        closeness = min(ratio_to_target, 1.0)
        # generous(타겟에 가까운 오퍼)일수록 고용주 인내심 낮게(δ_R 낮게)
        target_delta_R = 1.0 - 0.5 * closeness
        s.delta_R = 0.7 * s.delta_R + 0.3 * target_delta_R

        # 고용주가 추정하는 구직자의 할인율
        target_delta_E_hat = 1.0 - 0.3 * closeness
        s.delta_E_hat = 0.8 * s.delta_E_hat + 0.2 * target_delta_E_hat

    # 2) 지금 턴이 누구인지
    def current_player(self) -> str:
        """
        현재 라운드에서 제안해야 하는 플레이어('employee' or 'employer')를 반환합니다.
        first_mover 기준으로 라운드를 번갈아 가며 결정합니다.
        """
        s = self.state
        if s.first_mover == "employee":
            return "employee" if s.current_round % 2 == 1 else "employer"
        else:
            return "employer" if s.current_round % 2 == 1 else "employee"

    # 3) employee 턴일 때, 지금 얼마를 제안할지 계산
    def _suggest_employee_offer(self) -> float:
        s = self.state

        # 1) 루빈스타인 기반 게임 생성
        game = SalaryBargainingGame(
            B=s.B,
            S=s.S_target,
            E=s.E_max,
            delta_e=s.delta_E,
            delta_r=s.delta_R,
            first_mover=s.first_mover,
            horizon=s.remaining_rounds(),   # 남은 라운드 수만큼 역산
        )

        # 2) 타임라인 상에서 마지막 제안자는 회사(employer)로 가정
        path = game.compute_equilibrium_path(last_mover="employer")

        # 3) 현재 라운드에 대응되는 round_index 계산
        #    남은 라운드가 r개라면, 지금은 t-(r-1)에 해당
        current_index = -(s.remaining_rounds() - 1)

        # 4) 그 index에서 employee가 제안하는 상태 찾기
        #    (혹시 못 찾으면 가장 t에 가까운 employee state로 fallback)
        try:
            candidate = next(
                stt for stt in path
                if stt.round_index == current_index and stt.proposer == "employee"
            )
        except StopIteration:
            employee_states = [stt for stt in path if stt.proposer == "employee"]
            if not employee_states:
                # 이론적으로 거의 없지만, 방어적으로 S_target 근처 반환
                return max(s.B, min(s.S_target, s.E_max))
            candidate = max(employee_states, key=lambda stt: stt.round_index)

        # 5) 이론적인 제안 연봉 (루빈스타인 균형 값)
        offer_theoretical = s.B + s.pi * candidate.W_e
        offer_theoretical = max(s.B, min(offer_theoretical, s.E_max))

        # 6) 현실 보정: "첫 employee 오퍼"일 때만
        #    → S_target보다 "조금 더 높은 구간"에서 시작하도록 앵커링
        offer = offer_theoretical
        if len(s.history_employee) == 0:
            # S보다 약간 더 높은 범위 설정 (예: +3% ~ +15%)
            min_anchor = s.S_target * 1.03
            max_anchor = min(s.S_target * 1.15, s.E_max)
            if offer < min_anchor:
                offer = min_anchor
            elif offer > max_anchor:
                offer = max_anchor

        # 7) 최종적으로 [B, E_max] 범위로 한 번 더 클램프
        offer = max(s.B, min(offer, s.E_max))

        # 8) 항상 일정 비율 이상은 내려가도록 강제
        if s.history_employee:
            prev = s.history_employee[-1]
            min_concession_rate = 0.01  # 1% 양보

            # 이번 라운드에서 허용되는 최대 오퍼 (지난번 대비 최소 1% 인하)
            cap = prev * (1 - min_concession_rate)

            # 이론 오퍼(offer)가 cap보다 크면 cap까지 깎고,
            # cap보다 작으면 이론 오퍼(더 많이 깎는 것)를 그대로 사용
            new_offer = min(offer, cap, prev)

            # B 이하로는 내려가지 않도록 바닥 설정
            offer = max(s.B, new_offer)

        return offer

    # 4) 한 턴 진행: (필요하면 employer 오퍼 먼저 넣고) 내 제안 계산
    def next_employee_offer(self, employer_offer: Optional[float] = None) -> float:
        """
        실제 사용 패턴:
        - 고용주가 이번 라운드에 오퍼를 냈다면 employer_offer에 넣고 호출합니다.
        - 내부에서 해당 오퍼를 반영한 뒤,
        - employee 턴이 올 때까지 current_round를 조정하고,
        - 이번 employee 제안을 계산해 반환합니다.
        """
        s = self.state

        # 1) employer 오퍼가 들어왔다면 반영
        if employer_offer is not None:
            self.observe_employer_offer(employer_offer)

        # 2) current_round를 employee 턴이 될 때까지 증가
        while self.current_player() != "employee" and s.current_round <= s.total_rounds:
            s.current_round += 1

        # 라운드를 모두 사용했다면 더 이상 진행하지 않음
        if s.current_round > s.total_rounds:
            return max(s.B, min(s.S_target, s.E_max))

        # 3) employee 제안 계산
        offer = self._suggest_employee_offer()
        s.history_employee.append(offer)

        # 🔹 라운드별 할인율/제안자 로그 기록 (타임라인용)
        self.delta_history.append({
            "round": s.current_round,
            "delta_E": s.delta_E,
            "delta_R": s.delta_R,
            "proposer": self.current_player(),  # 이 라운드 제안자
        })

        # 4) 이 라운드 사용 완료 -> 다음 라운드로
        s.current_round += 1
        return offer

    # 5) 디버깅/로그용: 현재 상태 요약
    def summary(self) -> str:
        s = self.state
        return (
            f"Round {s.current_round}/{s.total_rounds}, "
            f"current_player={self.current_player()}, "
            f"S_target={s.S_target}, B={s.B}, E_max={s.E_max}, "
            f"delta_E={s.delta_E:.3f}, delta_R={s.delta_R:.3f}, "
            f"delta_E_hat={s.delta_E_hat:.3f}, "
            f"history_employee={s.history_employee}, "
            f"history_employer={s.history_employer}"
        )


# ===================== 세션 상태 초기화 =====================
if "page" not in st.session_state:
    # p2: 이직 여부 결정, p3: 연봉협상 메뉴, p4: 협상 시뮬레이터
    st.session_state["page"] = "p2"

if "jc_result" not in st.session_state:
    st.session_state["jc_result"] = None

if "neg_model" not in st.session_state:
    st.session_state["neg_model"] = None

if "show_info" not in st.session_state:
    st.session_state["show_info"] = False


# ===================== 유틸 함수들 =====================
def fetch_corp_metrics(name: str) -> dict:
    """
    회사 데이터를 가져오되, 어떤 오류가 나도 스트림릿 앱이 죽지 않도록
    전부 try/except로 감싼 안전 버전입니다.
    """
    corp = (name or "").strip()
    if not corp:
        return {
            "metrics": {},
            "warnings": ["회사명이 입력되지 않았습니다."],
            "debug": {},
            "ok": False,
            "error": "회사명이 비어 있습니다.",
        }

    try:
        url = f"{API_BASE}?corp={requests.utils.quote(corp)}"
        res = requests.get(url, timeout=10)
        if not res.ok:
            msg = f"회사 데이터 API 호출 실패 (HTTP {res.status_code})입니다. DART 응답을 가져오지 못했습니다."
            return {
                "metrics": {},
                "warnings": [msg],
                "debug": {},
                "ok": False,
                "error": msg,
            }
        data = res.json()
    except Exception as e:
        msg = f"회사 데이터를 불러오는 중 오류가 발생했습니다: {e}"
        return {
            "metrics": {},
            "warnings": [msg],
            "debug": {},
            "ok": False,
            "error": msg,
        }

    ok = bool(data.get("ok"))
    metrics = data.get("metrics") or {}

    warnings = []
    if isinstance(data.get("warnings"), list):
        for w in data["warnings"]:
            if w:
                warnings.append(str(w))

    if not ok:
        err_msg = data.get("error") or "회사 데이터를 가져오지 못했습니다."
        warnings.append(str(err_msg))

    return {
        "metrics": metrics,
        "warnings": warnings,
        "debug": data.get("debug") or {},
        "ok": ok,
        "error": data.get("error"),
    }


def get_industry_growth(industry: str) -> float:
    """산업별 성장률을 가져옵니다. 없는 경우 3% 기본값을 사용합니다."""
    return INDUSTRY_GROWTH.get(industry, 0.03)


def company_factor(metrics: dict, industry_growth_fallback: float) -> float:
    """
    회사 지수 계산:
    - 매출 성장률(salesGrowth)을 우선 사용하고, 없으면 산업 성장률을 사용합니다.
    - 자산(assets)을 log10으로 스케일링해서 규모를 반영합니다.
    """
    sales_growth = metrics.get("salesGrowth")
    if isinstance(sales_growth, (int, float)):
        sg = float(sales_growth)
    else:
        sg = float(industry_growth_fallback)
    growth_component = 1.0 + sg

    size_component = 1.0
    assets = metrics.get("assets")
    if isinstance(assets, (int, float)) and assets > 0:
        lg = math.log10(float(assets))
        size_component = lg / 12.0

    return growth_component * size_component


def format_score(x: float) -> str:
    """점수 포맷: 소수 둘째 자리까지입니다."""
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
    HTML 2페이지(이직 여부 결정)에서 하던 Wp/Wk 계산 로직입니다.
    - 현재/이직 업종 성장률을 각각 반영합니다.
    - DART ok 여부와 상관없이 숫자만 되면 무조건 이직/잔류/보류 중 하나는 나오게 합니다.
    """
    if not current_industry or not target_industry:
        raise ValueError("현재 직종과 이직 고려 직종을 모두 선택해야 합니다.")
    if years < 0:
        raise ValueError("연차는 0 이상이어야 합니다.")
    if salary <= 0:
        raise ValueError("연봉은 0보다 커야 합니다.")
    if not current_corp.strip() or not next_corp.strip():
        raise ValueError("현재 기업과 이직 고려 기업명을 모두 입력해야 합니다.")

    # 1) 회사 데이터 호출
    now_info = fetch_corp_metrics(current_corp)
    next_info = fetch_corp_metrics(next_corp)

    now_metrics = now_info["metrics"]
    next_metrics = next_info["metrics"]
    now_ok = bool(now_info.get("ok"))
    next_ok = bool(next_info.get("ok"))

    # 2) 업종 성장률
    g_now_ind = get_industry_growth(current_industry)
    g_next_ind = get_industry_growth(target_industry)

    # 3) SpBase: 현재 vs 이직 업종을 분리해서 사용
    salary_scale = salary / 100_000_000  # 1억 기준
    sp_base_now = salary_scale * ((1.0 + g_now_ind) ** years)
    sp_base_next = salary_scale * ((1.0 + g_next_ind) ** years)

    # 4) 회사 계수
    factor_now = company_factor(now_metrics, g_now_ind)
    factor_next = company_factor(next_metrics, g_next_ind)

    # 5) 최종 Wp, Wk
    wp = sp_base_now * factor_now
    wk = sp_base_next * factor_next

    # 6) 숫자 기준으로만 의사결정 (API ok 여부는 경고로만 사용)
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
        "now_warnings": now_info["warnings"],
        "next_warnings": next_info["warnings"],
        "now_ok": now_ok,
        "next_ok": next_ok,
        "g_now_ind": g_now_ind,
        "g_next_ind": g_next_ind,
        # 호환용 + 디버깅용 둘 다 제공
        "sp_base": sp_base_now,
        "sp_base_now": sp_base_now,
        "sp_base_next": sp_base_next,
        "factor_now": factor_now,
        "factor_next": factor_next,
    }


def format_currency(x: float) -> str:
    """연봉 숫자 포맷 (원 단위, 천 단위 콤마)입니다."""
    if not math.isfinite(x):
        return "-"
    return f"{int(round(x)):,} 원"


def format_percent(x: float) -> str:
    if not math.isfinite(x):
        return "-"
    return f"{x * 100:.1f}%"


# ===================== 공통 헤더 (제목 + 인포 버튼) =====================
page = st.session_state["page"]

col_title, col_info = st.columns([8, 1])
with col_title:
    st.title("피이직대학 이직 상담소")
    if page == "p2":
        st.subheader("- 이직 여부 결정")
    elif page == "p3":
        st.subheader("- 연봉협상 메뉴")
    elif page == "p4":
        st.subheader("- 협상 시뮬레이터")
with col_info:
    # 오른쪽 상단 인포 버튼
    if st.button("ℹ️", key="info_button", help="이 프로그램의 계산 로직 설명을 봅니다."):
        st.session_state["show_info"] = not st.session_state.get("show_info", False)

st.markdown("---")

# 🔹 인포 영역: 여기만 ~다 체 유지
if st.session_state.get("show_info", False):
    with st.expander("이 프로그램의 계산 로직", expanded=True):
        st.markdown(
            """
**1. 이직 여부 결정(Wp, Wk) 계산 개요**

- 본 도구는 현재 회사와 이직 고려 회사를 비교하기 위하여 두 회사에 대한 **워크플레이스 지수(Wp, Wk)**를 산출한다.  
- 입력 변수는 **연차(년)**, **현재 연봉(원)**, **현재 회사명**, **이직 고려 회사명**, **현재 직종**, **이직 고려 직종**이다.  

1) 업종 성장률  
- 각 직종에 대해 미리 정의된 **산업별 평균 연봉 상승률**을 사용한다.  
- 현재 직종 성장률을 \\( g_{\\text{now}} \\), 이직 직종 성장률을 \\( g_{\\text{next}} \\)라 한다.  

2) SpBase 계산  
- 연봉을 1억 원 기준으로 스케일링한 뒤, 연차만큼 업종 성장률을 반영하여 다음과 같이 정의한다.  

\\[
\\text{SpBase}_{\\text{now}} = \\frac{\\text{Salary}}{100{,}000{,}000} (1 + g_{\\text{now}})^{\\text{years}}
\\]

\\[
\\text{SpBase}_{\\text{next}} = \\frac{\\text{Salary}}{100{,}000{,}000} (1 + g_{\\text{next}})^{\\text{years}}
\\]

3) 회사 계수(Company Factor)  
- DART API를 통하여 각 회사의 **자산(assets)**, **매출 성장률(salesGrowth)** 등의 지표를 가져온다.  
- 매출 성장률이 존재하면 이를, 존재하지 않으면 해당 업종 평균 성장률을 사용한다.  

\\[
\\text{growth component} = 1 + \\text{salesGrowth}
\\]

- 자산 규모는 로그 스케일로 축소하여 반영한다.  

\\[
\\text{size component} = \\frac{\\log_{10}(\\text{assets})}{12}
\\]

- 최종 회사 계수는 다음과 같다.  

\\[
\\text{Company Factor} = \\text{growth component} \\times \\text{size component}
\\]

4) 최종 Wp, Wk  
- 현재 회사와 이직 고려 회사에 대하여 각각 다음과 같이 정의한다.  

\\[
Wp = \\text{SpBase}_{\\text{now}} \\times \\text{Company Factor}_{\\text{now}}
\\]

\\[
Wk = \\text{SpBase}_{\\text{next}} \\times \\text{Company Factor}_{\\text{next}}
\\]

5) 의사결정 규칙  
- 두 값이 유한한 실수로 계산되었을 때,  
  - \\( Wk > Wp \\)이면 **“이직!”**  
  - \\( Wp > Wk \\)이면 **“잔류!”**  
  - 두 값이 거의 비슷하면 **“보류”**로 판정한다.  


---

**2. 연봉 협상 시뮬레이터 계산 개요**

- 연봉 협상 모듈은 루빈스타인(Rubinstein) 교섭 모형을 참고하여 구성하였다.  
- 두 행위자는 **구직자(employee)**와 **회사(employer)**이며, 협상 가능한 연봉 구간은 \\([B, E_{\\max}]\\)이다.  

1) 기본 파라미터  
- \\( S \\): 구직자가 목표로 하는 최종 연봉(희망 연봉)  
- \\( B \\): 구직자가 받아들일 수 있는 최소 연봉  
- \\( E_{\\max} \\): 해당 직종에서 고용주가 지불할 수 있는 최대 연봉  
- 협상의 전체 파이는 다음과 같이 정의한다.  

\\[
\\pi = E_{\\max} - B
\\]

2) 목표 몫 비율 x  
- 구직자가 최종 시점에 가져가고자 하는 파이의 비율은  

\\[
x = \\frac{S - B}{\\pi}
\\]

로 두며, \\( 0 \\le x \\le 1 \\) 범위 내에 있는지 검증한다.  

3) 할인율(δ) 설정  
- 협상에는 구직자 할인율 \\( \\delta_E \\), 회사 할인율 \\( \\delta_R \\),  
  그리고 회사가 추정하는 구직자 할인율 \\( \\hat{\\delta}_E \\)가 사용된다.  

- **구직자 선제(employee first)**인 경우  
  - 사용자가 슬라이더를 통해 \\( \\delta_E, \\delta_R \\)를 직접 설정한다.  

- **회사 선제(employer first)**인 경우  
  - 위의 \\( x = (S-B)/\\pi \\) 값을 이용하여,  
    - \\( x \\)가 클수록(구직자가 더 큰 몫을 원할수록) 구직자 할인율을 다소 낮게,  
    - 회사 할인율을 다소 높게 설정하도록 자동 계산한다.  

4) 라운드별 균형 경로 계산(요약)  
- SalaryBargainingGame 클래스는 최종 시점 \\( t \\)에서의 구직자 몫 \\( W_E(t) = x \\)를 기준점으로 두고,  
  t, t-1, t-2 … 방향으로 **역진행(backward induction)**을 수행한다.  
- 각 단계에서 제안자에 따라 다음 관계식을 번갈아 적용한다.  

- 구직자 제안 라운드:  

\\[
W_R(t-1) = 1 - \\delta_E W_E(t), \\quad
W_E(t-1) = 1 - W_R(t-1)
\\]

- 회사 제안 라운드:  

\\[
W_E(t-1) = 1 - \\delta_R W_R(t), \\quad
W_R(t-1) = 1 - W_E(t-1)
\\]

- 이렇게 얻은 \\( W_E \\) 값에 대하여 실제 제안 연봉은  

\\[
\\text{Offer} = B + \\pi W_E
\\]

로 환산한다.  

5) 현실 보정  
- 첫 구직자 제안 시에는 이론값을 그대로 사용하지 않고,  
  목표 연봉 \\( S \\)보다 약간 높은 구간(예: +3% ~ +15%)에서 시작하도록 조정하여  
  실제 협상과 유사한 앵커링 효과를 반영한다.  
- 이후 라운드에서는 직전 제안 대비 최소 일정 비율(예: 1%) 이상 양보하도록 제한하여,  
  제안 금액이 단절적으로 움직이지 않도록 한다.  

요약하면, 본 프로그램은 (1) 회사 및 업종 정보를 바탕으로 이직 여부를 정량적으로 비교하고,  
(2) 설정된 조건과 할인율에 따라 라운드별 연봉 협상 경로를 시뮬레이션하도록 설계되어 있다.
            """
        )

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
            current_corp = st.text_input("현재 기업", placeholder="예: 강원랜드")
        with col4:
            salary = st.number_input(
                "현재 연봉 (원)",
                min_value=1.0,
                max_value=5_000_000_000.0,
                value=50_000_000.0,
                step=1_000_000.0,
                format="%.0f",
            )
            next_corp = st.text_input("이직 기업", placeholder="예: 삼성전자")

        calc_submit = st.form_submit_button("계산")

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

                # 🔗 Wk(이직 고려 직종) → 협상용 직종 키로 세션에 저장
                mapped_field = INDUSTRY_TO_FIELD.get(target_ind)
                if mapped_field:
                    st.session_state["neg_field_from_wk"] = mapped_field

            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

    result = st.session_state["jc_result"]

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

    if result:
        decision = result["decision"]

        # DART 데이터 신뢰도 안내
        if (not result.get("now_ok", True)) or (not result.get("next_ok", True)):
            st.info(
                "⚠ 일부 회사 데이터가 DART에서 완전하게 조회되지 않아, "
                "업종 평균/기본값으로 보정된 추정치로 계산했다."
            )

        if decision == "잔류!":
            st.warning(
                "현재 회사의 Wp가 이직 회사의 Wk보다 높게 계산되었다.\n\n"
                "⚠️ 충분히 양호한 직장을 보유하고 있는 상황에서 이직을 결정하는 경우, "
                "비금전적 요소를 보다 면밀히 검토할 필요가 있다."
            )
        elif decision == "보류":
            st.info("두 회사의 지수가 유사하게 계산되었다. 워라밸, 조직문화 등 비금전적 요소를 추가로 고려하는 것이 바람직하다.")
        elif decision == "계산 불가":
            st.error("지수를 계산할 수 없다. 입력값과 회사 데이터(연봉, 연차 등)를 다시 확인할 필요가 있다.")

        if decision == "이직!":
            st.success("이직 회사의 Wk가 현재 회사의 Wp보다 높게 계산되었다.")
            move = st.button("이직! (연봉 협상 메뉴로 이동)")
            if move:
                st.session_state["page"] = "p3"
                st.rerun()
        else:
            st.info("이직! 결과가 나와야 연봉협상 메뉴로 이동할 수 있다.")
    with st.expander("계산 상세 보기 (SpBase, 회사 계수, DART 데이터 상태 등)"):
        if result:
            st.write(f"연차: `{years}` 년")
            st.write(f"현재 직종 성장률 g_now_ind: `{result['g_now_ind']:.4f}`")
            st.write(f"이직 직종 성장률 g_next_ind: `{result['g_next_ind']:.4f}`")
            st.write(f"SpBase_now = (연봉 / 1억) × (1 + g_now_ind)^연차 = `{result['sp_base_now']:.4f}`")
            st.write(f"SpBase_next = (연봉 / 1억) × (1 + g_next_ind)^연차 = `{result['sp_base_next']:.4f}`")
            st.write(f"현재 회사 계수 factor_now: `{result['factor_now']:.4f}`")
            st.write(f"이직 회사 계수 factor_next: `{result['factor_next']:.4f}`")

            st.markdown("#### 현재 회사 metrics")
            st.json(result["now_metrics"])
            if result.get("now_warnings"):
                st.markdown("**현재 회사 데이터 관련 안내**")
                for w in result["now_warnings"]:
                    st.markdown(f"- {w}")

            st.markdown("#### 이직 회사 metrics")
            st.json(result["next_metrics"])
            if result.get("next_warnings"):
                st.markdown("**이직 회사 데이터 관련 안내**")
                for w in result["next_warnings"]:
                    st.markdown(f"- {w}")

            st.markdown(
                """
                ---
                **공식 정리**
                - `SpBase_now = (연봉 / 100,000,000) × (1 + g_now_ind)^연차`
                - `SpBase_next = (연봉 / 100,000,000) × (1 + g_next_ind)^연차`
                - `Wp = SpBase_now × 회사계수(현재 회사)`
                - `Wk = SpBase_next × 회사계수(이직 회사)`
                - 회사계수:
                    - 성장률 컴포넌트: `1 + salesGrowth` *(없으면 산업성장률 사용)*
                    - 규모 컴포넌트: `log10(assets) / 12`
                    - 최종: `(1 + 성장률) × (규모 컴포넌트)`
                """
            )
        else:
            st.write("아직 계산된 결과가 없다.")

    # 🔴 1번 요구사항: 초기 화면에서 바로 연봉 협상 창으로 가는 버튼
    st.markdown("---")
    st.markdown("#### 이미 이직을 결정한 경우")
    st.caption("이직 여부는 이미 스스로 결정했고, **연봉 협상 연습만** 진행하고자 하는 경우 아래 버튼을 사용할 수 있다.")
    if st.button("연봉 협상 시뮬레이터로 바로 이동", key="go_p4_from_p2"):
        st.session_state["page"] = "p4"
        st.rerun()

# ===================== PAGE 3: 연봉협상 메뉴 =====================
elif page == "p3":
    if st.button("뒤로 (이직 여부 결정으로)", key="back_to_p2"):
        st.session_state["page"] = "p2"
        st.rerun()

    st.markdown("### 연봉협상 메뉴")

    st.markdown(
        """<div style="padding:16px;border-radius:16px;border:1px solid #ddd;">
        <h3>협상 시뮬레이터</h3>
        <p>회사 제안 → 나의 응답을 라운드별로 반복하여 연봉 협상 과정을 연습할 수 있다.</p>
        </div>""",
        unsafe_allow_html=True,
    )

    # 🔴 3번 요구사항: 상자와 버튼 사이 간격 확대
    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)

    if st.button("협상 시뮬레이터 들어가기", key="go_p4"):
        st.session_state["page"] = "p4"
        st.rerun()

# ===================== PAGE 4: 협상 시뮬레이터 (NegotiationModel 기반) =====================
elif page == "p4":
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p4"):
        st.session_state["page"] = "p3"
        st.rerun()

    st.markdown("### 협상 시뮬레이터 (게임이론 + 휴리스틱)")
    st.caption(
        "루빈스타인 모형에서 출발한 할인율(δ) 개념과 "
        "목표 연봉 S, 최소 수용 연봉 B, 직종별 최대 연봉 E_max를 바탕으로 "
        "라운드별 적정 제안 연봉을 계산하는 시뮬레이터이다."
    )

    # 0) 처음 들어왔을 때: 첫 제안자만 고르는 드롭다운만 보이게
    if "neg_first_mover" not in st.session_state or "neg_total_rounds" not in st.session_state:
        st.markdown("#### 🧩 누가 먼저 제안하나요?")
        first_choice = st.selectbox(
            "첫 제안자 선택",
            options=["구직자가 먼저 제안 (employee)", "회사가 먼저 제안 (employer)"],
            index=0,
        )
        if st.button("확인", key="first_mover_confirm"):
            if "구직자" in first_choice:
                st.session_state["neg_first_mover"] = "employee"
                st.session_state["neg_total_rounds"] = 3  # 구직자 선제 시 3라운드
            else:
                st.session_state["neg_first_mover"] = "employer"
                st.session_state["neg_total_rounds"] = 4  # 고용자 선제 시 4라운드
            st.success(
                f"첫 제안자: **{first_choice}**로 설정되었다. "
                f"(전체 라운드 수: {st.session_state['neg_total_rounds']})"
            )
            st.rerun()

        # 🔻 이 단계에서는 진짜로 '드롭다운만' 보이도록 여기서 종료
        st.stop()

    # 0-1) 이미 첫 제안자를 선택한 이후에는, 선택 결과만 보여주기
    first_mover = st.session_state["neg_first_mover"]        # "employee" or "employer"
    total_rounds_default = st.session_state["neg_total_rounds"]
    human_label = "구직자(employee)" if first_mover == "employee" else "회사(employer)"
    st.info(
        f"현재 설정된 첫 제안자: **{human_label}**  \n"
        f"전체 라운드 수: **{total_rounds_default}**"
    )

    # 필요하면 첫 제안자 선택을 다시 할 수 있는 버튼
    if st.button("첫 제안자 다시 선택하기", key="reset_first_mover"):
        for k in ["neg_first_mover", "neg_total_rounds", "neg_model"]:
            st.session_state.pop(k, None)
        st.rerun()

    # 1) 세션에서 모델 꺼내오기
    neg_model: Optional[NegotiationModel] = st.session_state.get("neg_model")

    # 2) 협상 기본 설정 폼
    with st.expander("🔧 협상 기본 설정", expanded=(neg_model is None)):
        with st.form("neg_init_form"):
            col1, col2 = st.columns(2)
            with col1:
                S_target = st.number_input(
                    "목표 최종 연봉 S (만원 단위 예: 7000 → 7,000만원)",
                    min_value=1000.0,
                    max_value=50_000.0,
                    value=7000.0,
                    step=100.0,
                    format="%.0f",
                )
                B = st.number_input(
                    "최소 수용 연봉 B (만원)",
                    min_value=1000.0,
                    max_value=50_000.0,
                    value=5000.0,
                    step=100.0,
                    format="%.0f",
                )
            with col2:
                # 🔗 직종 드롭다운: 한국어 라벨 적용 + Wk 직종을 기본값으로 사용
                field_keys = list(DEFAULT_E_BY_FIELD.keys())
                field_labels = [FIELD_LABELS.get(k, k) for k in field_keys]

                # 기본 인덱스는 0
                default_index = 0
                # 2페이지(Wk)에서 매핑된 직종이 있으면 기본값으로 사용
                mapped_field = st.session_state.get("neg_field_from_wk")
                if mapped_field in field_keys:
                    default_index = field_keys.index(mapped_field)

                # 실제 사용자에게 보이는 것은 '한국어 라벨'
                selected_label = st.selectbox(
                    "직종 (E_max 테이블 기준)",
                    options=field_labels,
                    index=default_index,
                )

                # 내부 연산에서 사용할 키는 field_keys의 index로 역변환
                field_name = field_keys[field_labels.index(selected_label)]

                if first_mover == "employee":
                    # ✅ 구직자 선제일 때: 슬라이더로 직접 설정
                    delta_E_default = st.slider(
                        "초기 구직자 할인율 δ_E",
                        min_value=0.50,
                        max_value=0.99,
                        value=0.95,
                        step=0.01,
                    )
                    delta_R_default = st.slider(
                        "초기 회사 할인율 δ_R",
                        min_value=0.50,
                        max_value=0.99,
                        value=0.95,
                        step=0.01,
                    )
                else:
                    # ✅ 고용자 선제일 때: 할인율은 x = (S-B)/π를 이용해 자동 계산
                    st.markdown(
                        "δ_E, δ_R(구직자/회사 할인율)은  \n"
                        "**S, B, E_max와 x = (S−B)/π** 관계식을 이용하여 "
                        "모형이 자동으로 계산한다."
                    )
                    # 폼 내부에서는 일단 None으로 두고, 아래 submitted 블록에서 실제 값 계산
                    delta_E_default = None
                    delta_R_default = None

            submitted = st.form_submit_button("새 협상 세션 시작")

        if submitted:
            try:
                # 🔹 first_mover에 따라 할인율 결정 방식 분기
                if first_mover == "employee":
                    # 구직자 선제: 사용자가 슬라이더로 정한 값 그대로 사용
                    delta_e = float(delta_E_default)
                    delta_r = float(delta_R_default)
                else:
                    # 고용자 선제: x = (S-B)/π를 이용해 자동 계산
                    E_max = DEFAULT_E_BY_FIELD[field_name]
                    pie = max(E_max - B, 1e-9)     # π = E - B
                    x = (S_target - B) / pie       # x = (S - B) / π
                    x = max(0.0, min(x, 1.0))      # 0 ≤ x ≤ 1로 클램프

                    # 👉 x가 클수록(= 구직자가 파이에서 많이 가져가고 싶을수록)
                    #    구직자는 조금 덜 인내적, 회사는 조금 더 인내적이라고 가정
                    #    (0.90 ~ 0.99 범위 안에서 변화)
                    delta_e = 0.90 + 0.09 * (1.0 - x)  # 구직자 할인율 δ_E
                    delta_r = 0.90 + 0.09 * x          # 회사 할인율   δ_R

                model = NegotiationModel(
                    S=S_target,
                    B=B,
                    field_name=field_name,
                    first_mover=first_mover,
                    total_rounds=int(total_rounds_default),
                    E_table=DEFAULT_E_BY_FIELD,
                    delta_E_default=delta_e,
                    delta_R_default=delta_r,
                )
                st.session_state["neg_model"] = model
                neg_model = model
                st.success(
                    "✅ 새 협상 세션이 초기화되었다.\n\n"
                    f"- 첫 제안자: **{human_label}**  \n"
                    f"- δ_E(구직자 할인율): **{model.state.delta_E:.3f}**  \n"
                    f"- δ_R(회사 할인율): **{model.state.delta_R:.3f}**"
                )
            except Exception as e:
                st.error(f"협상 모델 초기화 중 오류가 발생했다: {e}")

    # 3) 모델이 아직 없으면 안내 후 종료
    if neg_model is None:
        st.info("위에서 협상 기본 설정을 마친 뒤, 새 협상 세션을 시작할 필요가 있다.")
        st.stop()

    # 4) 현재 상태 요약 보여주기 (🔴 가독성 개선)
       # 4) 현재 상태 요약 보여주기 (가독성 개선 – HTML 카드 제거)
    st.markdown("#### 현재 협상 상태")

    s = neg_model.state
    current_player_label = (
        "구직자" if neg_model.current_player() == "employee" else "회사"
    )

    st.markdown(
        f"""
**📌 기본 설정**

- **라운드**: {s.current_round} / {s.total_rounds}  
- **현재 제안 차례**: {current_player_label}  
- **목표 연봉 S**: {s.S_target:,.0f} 만원  
- **최소 수용 연봉 B**: {s.B:,.0f} 만원  
- **직종별 최대 연봉 E_max**: {s.E_max:,.0f} 만원  

**📌 할인율**

- **δ_E (구직자)**: {s.delta_E:.3f}  
- **δ_R (회사)**: {s.delta_R:.3f}  
- **회사 관점 추정 δ_Ê**: {s.delta_E_hat:.3f}  

**📌 히스토리**

- **구직자 제안 히스토리**: `{s.history_employee}`  
- **회사 오퍼 히스토리**: `{s.history_employer}`  
        """,
        unsafe_allow_html=False,
    )

    # 5) 이번 라운드 회사 오퍼 입력 + 추천 제안 계산
    st.markdown("#### 이번 라운드 입력")
    with st.form("neg_round_form"):
        col1, col2 = st.columns(2)
        with col1:
            employer_offer = st.number_input(
                "이번 라운드에서 회사가 제안한 연봉 (만원)",
                min_value=0.0,
                max_value=100_000.0,
                value=6500.0,
                step=100.0,
                format="%.0f",
                help="회사 오퍼가 없다면 체크박스를 해제하고, 바로 나의 제안을 계산할 수 있다.",
            )
            has_employer_offer = st.checkbox(
                "이번 라운드에 회사 오퍼가 있었다",
                value=True,
            )
        with col2:
            run_step = st.form_submit_button("나의 추천 제안 계산하기")

    if run_step:
        try:
            # 회사 오퍼 있는지 여부 확인
            if not has_employer_offer:
                employer_offer_val = None
            else:
                employer_offer_val = employer_offer

            # ❗ 이미 라운드가 종료됐다면 계산 차단
            if neg_model.state.current_round > neg_model.state.total_rounds:
                st.error("⛔ 모든 라운드가 이미 종료되어 더 이상 협상을 진행할 수 없다.")
                st.stop()

            # 이번 라운드 employee 제안 계산
            suggested = neg_model.next_employee_offer(
                employer_offer=employer_offer_val
            )

            # 현재 라운드 상황 출력
            st.success(
                f"💡 이번 라운드에서 추천되는 나의 제안 연봉: **{suggested:,.0f} 만원**"
            )
            st.markdown(
                f"- 현재 라운드: **{neg_model.state.current_round - 1} / {neg_model.state.total_rounds}**  \n"
                f"- 남은 라운드 수: **{neg_model.state.remaining_rounds()}**  \n"
                f"- 회사 오퍼 히스토리: `{neg_model.state.history_employer}`  \n"
                f"- 나의 제안 히스토리: `{neg_model.state.history_employee}`"
            )

            # 🔥 모든 라운드 종료 시 최종 결과 출력
            if neg_model.state.current_round > neg_model.state.total_rounds:
                st.markdown("---")
                st.success("🎉 **모든 라운드가 종료되었다.**")

                # 최종 연봉: employee 마지막 제안 or S_target 근처 값
                final_offer = neg_model.state.history_employee[-1]

                st.markdown(
                    f"""
                    ### 🏁 최종 연봉 협상 결과  
                    - **최종 합의 예상 연봉:**  
                      💰 **{final_offer:,.0f} 만원**  
                    - **총 라운드:** {neg_model.state.total_rounds}회  
                    - 협상이 종료되었다.
                    """
                )
                # 입력폼/버튼 비활성화 위해 stop()
                st.stop()
        except Exception as e:
            st.error(f"제안 계산 중 오류가 발생했다: {e}")

       # 🔽 라운드별 할인율 변화 타임라인 출력 (가독성 개선)
    if hasattr(neg_model, "delta_history") and len(neg_model.delta_history) > 0:
        st.markdown("### 📘 라운드별 할인율 변화 타임라인")

        timeline_html = """
        <style>
            .timeline {
                border-left: 3px solid #bbb;
                margin-left: 10px;
                padding-left: 18px;
            }
            .timeline-entry {
                margin-bottom: 16px;
                position: relative;
                padding: 8px 12px;
                border-radius: 10px;
                background-color: #f7f7f9;
                box-shadow: 0 1px 2px rgba(0,0,0,0.04);
            }
            .timeline-entry:before {
                content: "";
                position: absolute;
                left: -14px;
                top: 14px;
                width: 10px;
                height: 10px;
                border-radius: 50%;
                background-color: #4f46e5;
            }
            .timeline-round {
                font-weight: 600;
                font-size: 0.98rem;
                margin-bottom: 4px;
            }
            .timeline-body {
                font-size: 0.9rem;
                color: #444;
            }
        </style>
        <div class="timeline">
        """

        for item in neg_model.delta_history:
            proposer_kor = "구직자" if item["proposer"] == "employee" else "회사"
            timeline_html += f"""
            <div class="timeline-entry">
                <div class="timeline-round">
                    Round {item['round']} — 제안자: {proposer_kor}
                </div>
                <div class="timeline-body">
                    δ_E(구직자) = {item['delta_E']:.3f},&nbsp;
                    δ_R(회사) = {item['delta_R']:.3f}
                </div>
            </div>
            """

        timeline_html += "</div>"

        # ✅ HTML을 실제로 렌더링하도록 설정
        st.markdown(timeline_html, unsafe_allow_html=True)

    # 6) 세션 리셋 버튼 (협상 상태만 리셋)
    if st.button("🔄 협상 세션 리셋", key="reset_neg_model"):
        st.session_state["neg_model"] = None
        st.rerun()

# ===================== (아래 클래스들은 건드리지 않고 그대로 둠) =====================
Actor = Literal["employee", "employer"]


@dataclass
class RoundState:
    """한 라운드의 균형 상태입니다."""
    round_index: int          # t, t-1, t-2 ... 같은 상대적 인덱스 (0이 최종 t)
    proposer: Actor           # 이 라운드에서 제안하는 쪽
    W_e: float                # 이 라운드에서 구직자가 가져가는 파이의 비율
    W_r: float                # 이 라운드에서 고용주가 가져가는 파이의 비율

    @property
    def is_employee_turn(self) -> bool:
        return self.proposer == "employee"


@dataclass
class SalaryBargainingGame:
    # ----- 입력 파라미터 -----
    B: float                 # 최소 허용 연봉
    S: float                 # 희망 연봉
    E: float                 # 고용주 최대 연봉
    delta_e: float           # 구직자 할인율 δ_E
    delta_r: float           # 고용주 할인율 δ_R
    first_mover: Actor       # 협상 시작 시 첫 제안자
    horizon: int = 3         # t 기준으로 몇 단계 앞에서 시작할지 (t-3, t-4 등)
    offer_history: List[Dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not (self.B < self.S <= self.E):
            raise ValueError("B < S ≤ E 관계가 성립해야 한다.")
        if not (0 < self.delta_e <= 1 and 0 < self.delta_r <= 1):
            raise ValueError("할인율(delta_e, delta_r)은 0과 1 사이여야 한다.")

    @property
    def pie(self) -> float:
        """협상의 전체 파이 π = E - B입니다."""
        return self.E - self.B

    @property
    def x_target(self) -> float:
        """최종 시점 t에서 구직자가 가져가고자 하는 파이의 비율 x입니다."""
        return (self.S - self.B) / self.pie

    def compute_equilibrium_path(
        self,
        last_mover: Actor = "employee",
    ) -> List[RoundState]:
        """
        사진 속 식 기반:
        - x = (S - B) / π 를 t 시점 구직자 몫 W_E(t)로 두고
        - t, t-1, t-2 ... 로 역진행하면서
          고용주/구직자 라운드마다
          W_R = 1 - δ_E W_E(t)  또는
          W_E = 1 - δ_R W_R(t)
          를 번갈아 적용한다.
        """
        # 최종 시점 t에서의 구직자 몫 (x), 고용주 몫
        W_e_next = self.x_target          # x = (S - B) / π
        W_r_next = 1.0 - W_e_next

        states: List[RoundState] = [
            RoundState(round_index=0, proposer=last_mover, W_e=W_e_next, W_r=W_r_next)
        ]

        proposer = last_mover  # t 시점 제안자

        # t-1, t-2, ... 역진행
        for step in range(1, self.horizon + 1):
            if proposer == "employee":
                # 바로 이전 라운드는 고용주 제안 라운드
                # W_R(t-1) = 1 - δ_E W_E(t)
                W_r = 1.0 - self.delta_e * W_e_next
                # W_E(t-1) = 1 - W_R(t-1)
                W_e = 1.0 - W_r
                proposer_prev: Actor = "employer"
            else:
                # proposer == "employer" → 이전 라운드는 구직자 제안 라운드
                # W_E(t-1) = 1 - δ_R W_R(t)
                W_e = 1.0 - self.delta_r * W_r_next
                # W_R(t-1) = 1 - W_E(t-1)
                W_r = 1.0 - W_e
                proposer_prev = "employee"

            states.append(
                RoundState(
                    round_index=-step,
                    proposer=proposer_prev,
                    W_e=W_e,
                    W_r=W_r,
                )
            )

            # 다음 역진행 스텝 준비 (t-1 → t-2 ...)
            W_e_next, W_r_next, proposer = W_e, W_r, proposer_prev

        # round_index 기준으로 정렬해서 반환
        states.sort(key=lambda s: s.round_index)
        return states

    def recommend_employee_offer(
        self,
        current_round_index: int,
        current_proposer: Actor,
    ) -> float:
        """
        current_round_index 기준으로, 지금 또는 다음 employee 차례의 추천 연봉을 계산한다.
        """
        path = self.compute_equilibrium_path(last_mover="employee")

        if current_proposer == "employee":
            candidate = max(
                (stt for stt in path if stt.round_index == current_round_index),
                key=lambda stt: stt.round_index,
            )
        else:
            candidate = max(
                (
                    stt
                    for stt in path
                    if stt.round_index >= current_round_index
                    and stt.proposer == "employee"
                ),
                key=lambda stt: stt.round_index,
            )

        W_e_now = candidate.W_e
        suggested_salary = self.B + self.pie * W_e_now
        return suggested_salary

    def record_offer(self, proposer: Actor, salary: float, round_index: int) -> None:
        self.offer_history.append(
            {
                "proposer": proposer,
                "salary": salary,
                "round_index": round_index,
                "share_for_employee": (salary - self.B) / self.pie,
            }
        )

    def update_deltas_from_history(self) -> None:
        """TODO: 히스토리를 기반으로 delta_e, delta_r 업데이트 로직이다."""
        pass
