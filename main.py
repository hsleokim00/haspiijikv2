import math
import requests
import streamlit as st

from dataclasses import dataclass, field
from typing import Literal, List, Dict, Optional   # 🔹 Optional 추가


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

# 직종별 고용주 최대 지불 의사 연봉 E_max (예시용; PAGE 5에서는 직접 숫자로 넣어서 사용)
DEFAULT_E_BY_FIELD: Dict[str, float] = {
    "it_dev": 9000.0,
    "medical": 12000.0,
    "driver": 6000.0,
    "service": 5000.0,
    "manufacturing": 7000.0,
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
        """현재 라운드를 포함해 앞으로 남은 전체 라운드 수."""
        return max(self.total_rounds - self.current_round + 1, 0)

    @property
    def pi(self) -> float:
        """협상의 파이 크기 π = E_max - B"""
        return self.E_max - self.B

    def target_share(self) -> float:
        """
        파이에서 구직자가 가져가고 싶은 비율 x = (S - B)/π.
        x ∈ [0,1] 범위인지 체크해서, 목표 연봉이 협상 구간 안인지 확인.
        """
        if self.pi <= 0:
            raise ValueError("E_max must be greater than B")
        return (self.S_target - self.B) / self.pi


class NegotiationModel:
    """
    실시간 연봉 협상 모델.
    - 상태(state)를 들고 있다가
    - employer 오퍼가 들어오면 할인율 등을 업데이트하고
    - employee 차례가 되면 '지금 얼마를 제안해야 하는지'를 계산해서 돌려준다.
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
            raise ValueError("first_mover must be 'employee' or 'employer'")

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
                f"S_target={S} is outside feasible range "
                f"[B={B}, E_max={E_max}] (x={x:.3f})"
            )

        self.state = state

    # 1) 고용주 오퍼 관찰 -> 상태 & 할인율 업데이트
    def observe_employer_offer(self, offer: float) -> None:
        """
        고용주가 새 오퍼를 했을 때 호출.
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
        현재 라운드에서 제안해야 하는 플레이어 ('employee' or 'employer').
        first_mover 기준으로 라운드를 번갈아 가며 결정한다.
        """
        s = self.state
        if s.first_mover == "employee":
            return "employee" if s.current_round % 2 == 1 else "employer"
        else:
            return "employer" if s.current_round % 2 == 1 else "employee"

    # 3) employee 턴일 때, 지금 얼마를 제안할지 계산
    def _suggest_employee_offer(self) -> float:
        """
        구직자의 현재 라운드 제안값을 계산.
        - S_target, B, E_max, delta_E, 남은 라운드 수,
          마지막 고용주 오퍼 등을 이용해
        - '타겟 S를 향해 얼마나 다가갈지(step)를 결정하는' 휴리스틱 모델
        """
        s = self.state

        remaining = s.remaining_rounds()
        if remaining <= 0:
            return s.S_target

        # 마지막 고용주 오퍼 (없으면 B 기준)
        last_emp_offer = s.history_employer[-1] if s.history_employer else s.B

        # 타겟까지 남은 거리
        gap_to_target = s.S_target - last_emp_offer

        # 구직자 인내심: delta_E가 낮을수록 급함
        urgency = 1.0 - s.delta_E

        # 남은 라운드가 적을수록 더 크게 움직이도록
        round_factor = 1.0 / remaining

        # 이번에 gap의 몇 %를 움직일지 결정 (최소 10%, 최대 90%)
        step_ratio = 0.5 * urgency + 0.5 * round_factor
        step_ratio = max(0.1, min(step_ratio, 0.9))

        offer = last_emp_offer + step_ratio * gap_to_target

        # B~E_max 사이로 클램프
        offer = max(s.B, min(offer, s.E_max))

        return offer

    # 4) 한 턴 진행: (필요하면 employer 오퍼 먼저 넣고) 내 제안 계산
    def next_employee_offer(self, employer_offer: Optional[float] = None) -> float:
        """
        실제 사용 패턴:
        - 고용주가 이번 라운드에 오퍼를 냈다면 employer_offer에 넣고 호출
        - 내부에서 해당 오퍼를 반영한 뒤,
        - employee 턴이 올 때까지 current_round를 조정하고,
        - 이번 employee 제안을 계산해 반환한다.
        """
        s = self.state

        # 1) employer 오퍼가 들어왔다면 반영
        if employer_offer is not None:
            self.observe_employer_offer(employer_offer)

        # 2) current_round를 employee 턴이 될 때까지 증가
        while self.current_player() != "employee" and s.current_round <= s.total_rounds:
            s.current_round += 1

        if s.current_round > s.total_rounds:
            return s.S_target

        # 3) employee 제안 계산
        offer = self._suggest_employee_offer()
        s.history_employee.append(offer)

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
    # p2: 이직 여부 결정, p3: 연봉협상 메뉴, p5: 연봉 협상 시뮬레이터, p4: 초기 연봉 제시
    st.session_state["page"] = "p2"

if "jc_result" not in st.session_state:
    st.session_state["jc_result"] = None

if "neg_result" not in st.session_state:
    st.session_state["neg_result"] = None

if "initial_offer_result" not in st.session_state:
    st.session_state["initial_offer_result"] = None


# ===================== 로직 함수들 =====================
def fetch_corp_metrics(name: str) -> dict:
    """
    회사 데이터를 가져오되, 어떤 오류가 나도 스트림릿 앱이 죽지 않도록
    전부 try/except로 감싼 안전 버전.
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
            msg = f"회사 데이터 API 호출 실패 (HTTP {res.status_code}). DART 응답을 가져오지 못했습니다."
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
    """산업별 성장률 가져오기. 없는 경우 3% 기본값."""
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

    growth_component = 1.0 + sg

    size_component = 1.0
    assets = metrics.get("assets")
    if isinstance(assets, (int, float)) and assets > 0:
        lg = math.log10(float(assets))
        size_component = lg / 12.0

    return growth_component * size_component


def format_score(x: float) -> str:
    """점수 포맷: 소수 둘째 자리까지."""
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
    - 현재/이직 업종 성장률을 각각 반영
    - DART ok 여부와 상관없이 숫자만 되면 무조건 이직/잔류/보류 중 하나는 나오게 함
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


def compute_rubinstein_equilibrium(
    min_salary: float,
    max_salary: float,
    delta_worker: float,
    delta_firm: float,
):
    """
    Rubinstein 모형을 이용한 연봉 협상 균형 계산.
    """
    if min_salary <= 0 or max_salary <= 0:
        raise ValueError("연봉은 0보다 커야 합니다.")
    if max_salary <= min_salary:
        raise ValueError("회사 최대 지불 의사가 최소 수용 연봉보다 커야 합니다.")
    if not (0 < delta_worker < 1) or not (0 < delta_firm < 1):
        raise ValueError("할인 계수 δ는 0과 1 사이의 값이어야 합니다.")

    pie = max_salary - min_salary
    share_worker = (1.0 - delta_firm) / (1.0 - delta_worker * delta_firm)
    share_worker = max(0.0, min(1.0, share_worker))

    salary_worker = min_salary + share_worker * pie
    share_firm = 1.0 - share_worker
    surplus_firm = max_salary - salary_worker

    return {
        "pie": pie,
        "share_worker": share_worker,
        "share_firm": share_firm,
        "salary_worker": salary_worker,
        "surplus_firm": surplus_firm,
    }


def format_currency(x: float) -> str:
    """연봉 숫자 포맷 (원 단위, 천 단위 콤마)."""
    if not math.isfinite(x):
        return "-"
    return f"{int(round(x)):,} 원"


def format_percent(x: float) -> str:
    if not math.isfinite(x):
        return "-"
    return f"{x * 100:.1f}%"


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
                "업종 평균/기본값으로 보정된 추정치입니다."
            )

        if decision == "잔류!":
            st.warning(
                "현재 회사의 Wp가 이직 회사의 Wk보다 높게 계산되었습니다.\n\n"
                "⚠️ 충분히 좋은 직장을 두고 왜 이직하시죠...?"
            )
        elif decision == "보류":
            st.info("두 회사의 지수가 거의 비슷합니다. 다른 요소(워라밸, 조직문화 등)를 더 고려해 보세요.")
        elif decision == "계산 불가":
            st.error("지수를 계산할 수 없습니다. 입력값과 회사 데이터(연봉, 연차 등)를 다시 확인해 주세요.")

        if decision == "이직!":
            st.success("이직 회사의 Wk가 현재 회사의 Wp보다 높게 계산되었습니다.")
            move = st.button("이직! (연봉 협상 메뉴로 이동)")
            if move:
                st.session_state["page"] = "p3"
                st.rerun()
        else:
            st.info("이직! 결과가 나와야 연봉협상 메뉴로 이동할 수 있습니다.")

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
            st.write("아직 계산된 결과가 없습니다.")


# ===================== PAGE 3: 연봉협상 메뉴 =====================
elif page == "p3":
    if st.button("뒤로 (이직 여부 결정으로)", key="back_to_p2"):
        st.session_state["page"] = "p2"
        st.rerun()

    st.markdown("### 연봉협상 메뉴")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """<div style="padding:16px;border-radius:16px;border:1px solid #ddd;">
            <h3>연봉 협상 시뮬레이터</h3>
            <p>회사 제안 → 나의 응답을 라운드별로 돌려보며 협상을 연습합니다.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("들어가기", key="go_p5"):
            st.session_state["page"] = "p5"
            st.rerun()

    with col2:
        st.markdown(
            """<div style="padding:16px;border-radius:16px;border:1px solid #ddd;">
            <h3>초기 연봉 제시</h3>
            <p>이론상 최적 최초 제시 연봉(첫 오퍼)을 계산합니다.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("들어가기", key="go_p4"):
            st.session_state["page"] = "p4"
            st.rerun()

# ===================== PAGE 5: 할인율 δ_E, δ_R 기반 협상 시뮬레이터 =====================
elif page == "p5":
    # 🔙 연봉협상 메뉴로 돌아가기 버튼
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p5"):
        st.session_state["page"] = "p3"
        st.rerun()

    st.markdown("### 협상 라운드 시뮬레이터 (할인율 δ 기반)")
    st.caption(
        "구직자 할인율 δ_E, 기업 할인율 δ_R을 기반으로 루빈스타인 균형 연봉을 계산하고,\n"
        "회사와 구직자가 번갈아 제안/수락하는 협상 과정을 시뮬레이션합니다.\n\n"
        "※ employer가 먼저 시작하면: 1라운드에서 회사 제안 → 수락/거절\n"
        "※ employee가 먼저 시작하면: 사용자가 먼저 연봉 제시 → 회사가 수락/재제안"
    )

    # ---------------- 세션 초기화 ----------------
    if "neg_state" not in st.session_state:
        st.session_state["neg_state"] = None

    neg_state = st.session_state["neg_state"]

    # ---------------- 루빈스타인 균형 공식 ----------------
    def compute_rubinstein_salary(B, E, delta_E, delta_R):
        """
        S* = B + v_W (E-B)
        v_W = (1 - δ_R) / (1 - δ_E δ_R)
        """
        if not (0 < delta_E < 1 and 0 < delta_R < 1):
            raise ValueError("할인율은 0과 1 사이여야 합니다.")

        pie = E - B
        v_W = (1 - delta_R) / (1 - delta_E * delta_R)
        v_W = max(0.0, min(1.0, v_W))   # 안전 클램프

        S_star = B + v_W * pie
        return S_star, v_W, 1 - v_W

    # ---------------- 회사 제안 규칙 ----------------
    def compute_employer_offer(B, E, S_star, last_employee_offer):
        """
        회사는:
        - employee 오퍼가 처음이면, 바로 S* 제안
        - 아니면 employee 오퍼와 S*의 중간값 제안
        """
        S_star_clamped = max(B, min(E, S_star))

        if last_employee_offer is None:
            return S_star_clamped

        offer = last_employee_offer + 0.5 * (S_star_clamped - last_employee_offer)
        offer = max(B, min(E, offer))
        return offer

    # ---------------- 설정 폼 ----------------
    with st.expander("🔧 협상 기본 설정", expanded=neg_state is None):
        with st.form("neg_init_form"):
            col1, col2 = st.columns(2)
            with col1:
                B = st.number_input("최소 수용 연봉 B", 1_000_000, 5_000_000_000, 50_000_000)
                max_rounds = st.number_input("최대 라운드 수", 1, 10, 4)
            with col2:
                E = st.number_input("회사 최대 지불 의사 연봉 E", 1_000_000, 5_000_000_000, 80_000_000)
                delta_E = st.slider("구직자 할인율 δ_E", 0.5, 0.99, 0.95, step=0.01)
                delta_R = st.slider("기업 할인율 δ_R", 0.5, 0.99, 0.90, step=0.01)

            first_mover = st.selectbox(
                "첫 제안자",
                ["employer", "employee"],
                format_func=lambda x: "회사(employer)" if x == "employer" else "구직자(employee)",
            )

            submitted = st.form_submit_button("새 협상 시작")

        if submitted:
            if B >= E:
                st.error("B는 E보다 작아야 합니다.")
            else:
                S_star, share_E, share_R = compute_rubinstein_salary(B, E, delta_E, delta_R)

                st.session_state["neg_state"] = {
                    "B": B,
                    "E": E,
                    "delta_E": delta_E,
                    "delta_R": delta_R,
                    "S_star": S_star,
                    "share_E": share_E,
                    "share_R": share_R,
                    "max_rounds": int(max_rounds),
                    "first_mover": first_mover,

                    "current_round": 1,
                    "turn": first_mover,
                    "status": "ongoing",

                    "last_employee_offer": None,
                    "last_employer_offer": None,
                    "final_salary": None,
                }
                neg_state = st.session_state["neg_state"]

    # ---------------- 설정 완료 전이면 종료 ----------------
    if neg_state is None:
        st.info("위에서 연봉 B/E, 할인율 δ_E/δ_R 등을 설정해 주세요.")
        st.stop()

    # state unpack
    B = neg_state["B"]
    E = neg_state["E"]
    delta_E = neg_state["delta_E"]
    delta_R = neg_state["delta_R"]
    S_star = neg_state["S_star"]

    current_round = neg_state["current_round"]
    max_rounds = neg_state["max_rounds"]
    turn = neg_state["turn"]
    status = neg_state["status"]

    # ---------------- 현재 상태 표시 ----------------
    st.markdown(
        f"**라운드:** {current_round} / {max_rounds}  &nbsp;|&nbsp; "
        f"**루빈스타인 균형 연봉 S\***: {S_star:,.0f} 원"
    )
    st.caption(
        f"구직자 할인율 δ_E = {delta_E:.2f}, 기업 할인율 δ_R = {delta_R:.2f}  \n"
        f"근로자 몫 비율 = {neg_state['share_E']:.3f}, 회사 몫 비율 = {neg_state['share_R']:.3f}"
    )

    st.markdown("---")

    # ---------------- 종료 상태 ----------------
    if status in ("success", "failed"):
        if status == "success":
            st.success(f"🎉 협상 성공! 최종 합의 연봉: **{neg_state['final_salary']:,.0f} 원**")
        else:
            st.error("❌ 협상 실패 (라운드 초과)")

        if st.button("새 협상 시작하기"):
            st.session_state["neg_state"] = None
            st.rerun()
        st.stop()

    # ---------------- 라운드 증가 함수 ----------------
    def next_round():
        neg_state["current_round"] += 1
        if neg_state["current_round"] > neg_state["max_rounds"]:
            neg_state["status"] = "failed"

    # ---------------- TURN: employer ----------------
    if turn == "employer":
        employer_offer = compute_employer_offer(
            B, E, S_star, neg_state["last_employee_offer"]
        )
        neg_state["last_employer_offer"] = employer_offer

        st.markdown("### 🏢 회사의 제안")
        st.markdown(f"이번 라운드 회사 제안: **{employer_offer:,.0f} 원**")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ 수락"):
                neg_state["status"] = "success"
                neg_state["final_salary"] = employer_offer
                st.rerun()
        with col2:
            if st.button("❌ 거절하고 다음 라운드"):
                next_round()
                if neg_state["status"] == "failed":
                    st.rerun()
                neg_state["turn"] = "employee"
                st.rerun()

    # ---------------- TURN: employee ----------------
    else:
        st.markdown("### 👤 구직자의 제안")
        st.markdown("연봉을 입력하세요. 회사가 수락 가능(B~E)이면 즉시 협상 종료됩니다.")

        with st.form("employee_form"):
            emp_offer = st.number_input(
                "제안 연봉",
                min_value=1_000_000,
                max_value=5_000_000_000,
                value=int(S_star),
                step=1_000_000,
            )
            send = st.form_submit_button("제안하기")

        if send:
            neg_state["last_employee_offer"] = emp_offer

            if B <= emp_offer <= E:
                neg_state["status"] = "success"
                neg_state["final_salary"] = emp_offer
                st.rerun()
            else:
                employer_counter = compute_employer_offer(
                    B, E, S_star, emp_offer
                )
                neg_state["last_employer_offer"] = employer_counter

                next_round()
                if neg_state["status"] == "failed":
                    st.rerun()

                neg_state["turn"] = "employer"
                st.rerun()

    # ---------------- 리셋 버튼 ----------------
    st.markdown("---")
    if st.button("🔄 초기 설정으로 돌아가기"):
        st.session_state["neg_state"] = None
        st.rerun()


# ===================== PAGE 4: 초기 연봉 제시 (SPE 기반) =====================
elif page == "p4":
    # 🔙 연봉협상 메뉴로 돌아가기 버튼
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p4"):
        st.session_state["page"] = "p3"
        st.rerun()

    st.markdown("### 초기 연봉 제시 (SPE 기반)")
    st.caption(
        "희망하는 최종 연봉 S*, 회사가 제시할 수 있는 최대 연봉 E, "
        "구직자/기업의 할인율(δ_E, δ_R)을 기반으로\n"
        "루빈스타인 모형의 균형 (SPE)이 성립하도록 하는 최소 수용 연봉 B를 역산하고, "
        "그때의 최초 제시 연봉(=최종 연봉)을 보여줍니다."
    )

    def compute_spe_from_target(
        S_target: float,
        E_max: float,
        delta_worker: float,
        delta_firm: float,
    ):
        """
        입력: 목표 최종 연봉 S_target, 회사 최대 연봉 E_max, δ_E, δ_R
        루빈스타인 SPE 공식:
            v_W = (1 - δ_R) / (1 - δ_E δ_R)
            S* = B + v_W (E - B)  (여기서 S* = S_target, E = E_max)
        를 이용해서 B를 역산:
            S* = v_W E + (1 - v_W) B
            (1 - v_W) B = S* - v_W E
            B = (S* - v_W E) / (1 - v_W)
        """
        if S_target <= 0 or E_max <= 0:
            raise ValueError("연봉은 0보다 커야 합니다.")
        if not (0 < delta_worker < 1 and 0 < delta_firm < 1):
            raise ValueError("할인율 δ_E, δ_R은 0과 1 사이여야 합니다.")

        # 근로자 몫 비율 v_W
        v_W = (1.0 - delta_firm) / (1.0 - delta_worker * delta_firm)
        v_W = max(0.0, min(1.0, v_W))  # 안전 클램프

        denom = 1.0 - v_W
        if abs(denom) < 1e-9:
            raise ValueError("할인율 조합이 v_W ≈ 1이 되어, B를 정의하기 어렵습니다.")

        B = (S_target - v_W * E_max) / denom

        # 일관성 체크: B < S* ≤ E 여야 함
        if B >= S_target:
            raise ValueError("이 할인율과 최대 연봉 조합으로는 S*가 최소 수용 연봉보다 높게 설정될 수 없습니다.")
        if S_target > E_max:
            raise ValueError("희망 최종 연봉 S*는 회사 최대 연봉 E보다 클 수 없습니다.")
        if B <= 0:
            raise ValueError("역산된 최소 수용 연봉 B가 0 이하입니다. 입력값을 다시 조정해 주세요.")

        pie = E_max - B
        # 이론상 share_worker는 v_W와 일치해야 함
        share_worker = (S_target - B) / pie
        share_firm = 1.0 - share_worker
        firm_surplus = E_max - S_target
        worker_surplus = S_target - B

        return {
            "B": B,
            "E": E_max,
            "S_target": S_target,
            "delta_worker": delta_worker,
            "delta_firm": delta_firm,
            "share_worker": share_worker,
            "share_firm": share_firm,
            "worker_surplus": worker_surplus,
            "firm_surplus": firm_surplus,
            # SPE에서 근로자가 먼저 제안하면 최초 제시 연봉 = 최종 연봉 = S*
            "initial_offer": S_target,
        }

    if "initial_offer_result" not in st.session_state:
        st.session_state["initial_offer_result"] = None

    with st.form("initial_offer_form_spe"):
        col1, col2 = st.columns(2)
        with col1:
            S_target = st.number_input(
                "희망하는 최종 연봉 S* (원)",
                min_value=1_000_000.0,
                max_value=5_000_000_000.0,
                value=65_000_000.0,
                step=1_000_000.0,
                format="%.0f",
                key="S_target",
            )
            delta_worker0 = st.slider(
                "구직자 할인율 δ_E",
                min_value=0.50,
                max_value=0.99,
                value=0.95,
                step=0.01,
                key="delta_worker0",
            )
        with col2:
            E_max0 = st.number_input(
                "회사가 오퍼할 수 있는 최대 연봉 E (원)",
                min_value=1_000_000.0,
                max_value=5_000_000_000.0,
                value=80_000_000.0,
                step=1_000_000.0,
                format="%.0f",
                key="E_max0",
            )
            delta_firm0 = st.slider(
                "기업 할인율 δ_R",
                min_value=0.50,
                max_value=0.99,
                value=0.90,
                step=0.01,
                key="delta_firm0",
            )

        submitted_init = st.form_submit_button("SPE 기준 최초 제시 연봉 계산")

    if submitted_init:
        try:
            init_res = compute_spe_from_target(
                S_target=S_target,
                E_max=E_max0,
                delta_worker=delta_worker0,
                delta_firm=delta_firm0,
            )
            st.session_state["initial_offer_result"] = init_res
        except Exception as e:
            st.session_state["initial_offer_result"] = None
            st.error(f"오류가 발생했습니다: {e}")

    init_res = st.session_state["initial_offer_result"]

    if init_res:
        initial_offer = init_res["initial_offer"]

        # 🔳 검은 상자 + 큰 글씨 UI
        st.markdown(
            f"""
            <div style="padding:24px;border-radius:18px;border:2px solid #000;
                        background-color:#111;color:#fff;text-align:center;">
                <div style="font-size:0.95rem;margin-bottom:10px;opacity:0.8;">
                    SPE(루빈스타인 균형) 기준 추천 최초 제시 연봉
                </div>
                <div style="font-size:2rem;font-weight:700;">
                    {format_currency(initial_offer)}
                </div>
                <div style="margin-top:10px;font-size:0.95rem;opacity:0.9;">
                    (희망 최종 연봉 S* = {format_currency(init_res['S_target'])})
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("---")
        st.markdown("#### 균형 구조 해석")

        st.write(
            f"- 역산된 **최소 수용 연봉 B**: {format_currency(init_res['B'])}  \n"
            f"- 회사 최대 지불 연봉 E: {format_currency(init_res['E'])}"
        )
        st.write(
            f"- 근로자 몫 비율: {format_percent(init_res['share_worker'])}  \n"
            f"- 회사 몫 비율: {format_percent(init_res['share_firm'])}"
        )
        st.write(
            f"- 근로자 잉여 (S* - B): {format_currency(init_res['worker_surplus'])}  \n"
            f"- 회사 잉여 (E - S*): {format_currency(init_res['firm_surplus'])}"
        )

        with st.expander("수식 자세히 보기"):
            st.markdown(
                r"""
                **1. 루빈스타인 모형의 SPE (무한 교대제안)**  

                - 구직자 할인율: \( \delta_E \)  
                - 기업 할인율: \( \delta_R \)  

                근로자 몫 비율 \( v_W \) 는  
                \[
                  v_W = \frac{1 - \delta_R}{1 - \delta_E \delta_R}
                \]

                회사의 최대 지불 연봉을 \( E \), 최소 수용 연봉을 \( B \) 라고 하면,  
                균형 최종 연봉 \( S^* \) 는
                \[
                  S^* = B + v_W (E - B)
                \]

                **2. 이번 계산기에서 하는 일**

                사용자가
                - 희망 최종 연봉 \( S^* \),
                - 회사 최대 연봉 \( E \),
                - \( \delta_E, \delta_R \)

                를 정해 주면, 위 식을 **역으로 풀어** \( B \) 를 구합니다.
                \[
                  S^* = v_W E + (1 - v_W) B
                \Rightarrow
                  B = \frac{S^* - v_W E}{1 - v_W}
                \]

                이렇게 얻은 \( B \) 에 대해 루빈스타인 SPE를 적용하면,  
                **근로자가 처음 제시하는 연봉 = 최종 연봉 = \( S^* \)** 가 됩니다.
                """
            )
    else:
        st.info("입력값을 설정한 뒤 'SPE 기준 최초 제시 연봉 계산' 버튼을 눌러 결과를 확인하세요.")

# ===================== PAGE 4: 초기 연봉 제시 (B 기반 SPE 계산) =====================
elif page == "p4":
    # 🔙 연봉협상 메뉴로 돌아가기
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p4"):
        st.session_state["page"] = "p3"
        st.rerun()

    st.markdown("### 초기 연봉 제시 (루빈스타인 SPE 기반)")
    st.caption(
        "나의 최소 수용 연봉 B, 회사가 지불할 수 있는 최대 연봉 E, "
        "구직자/기업의 할인율(δ_E, δ_R)을 입력하면\n"
        "루빈스타인 모형의 균형(SPE)에 따라 **최초 제시 연봉(=최종 합의 연봉)**을 계산합니다."
    )

    # 결과 저장용
    if "initial_offer_result" not in st.session_state:
        st.session_state["initial_offer_result"] = None

    with st.form("initial_offer_form_B"):
        col1, col2 = st.columns(2)

        with col1:
            min_salary0 = st.number_input(
                "나의 최소 수용 연봉 B (원)",
                min_value=1.0,
                max_value=5_000_000_000.0,
                value=50_000_000.0,
                step=1_000_000.0,
                format="%.0f",
                key="min_salary0",
            )
            delta_worker0 = st.slider(
                "구직자 할인율 δ_E",
                min_value=0.50,
                max_value=0.99,
                value=0.95,
                step=0.01,
                key="delta_worker0",
            )

        with col2:
            max_salary0 = st.number_input(
                "회사의 최대 지불 의사 연봉 E (원)",
                min_value=1.0,
                max_value=5_000_000_000.0,
                value=80_000_000.0,
                step=1_000_000.0,
                format="%.0f",
                key="max_salary0",
            )
            delta_firm0 = st.slider(
                "기업 할인율 δ_R",
                min_value=0.50,
                max_value=0.99,
                value=0.90,
                step=0.01,
                key="delta_firm0",
            )

        submitted_init = st.form_submit_button("SPE 기준 최초 제시 연봉 계산")

    if submitted_init:
        try:
            # 🔹 여기서 위쪽에 이미 정의된 루빈스타인 함수 사용
            init_res = compute_rubinstein_equilibrium(
                min_salary=min_salary0,
                max_salary=max_salary0,
                delta_worker=delta_worker0,
                delta_firm=delta_firm0,
            )
            st.session_state["initial_offer_result"] = init_res
        except Exception as e:
            st.session_state["initial_offer_result"] = None
            st.error(f"오류가 발생했습니다: {e}")

    init_res = st.session_state["initial_offer_result"]

    if init_res:
        salary_star = init_res["salary_worker"]      # 균형 연봉 = 추천 최초 제시 연봉
        share_worker = init_res["share_worker"]
        share_firm = init_res["share_firm"]

        # 🔳 검은 상자 + 큰 글씨 UI
        st.markdown(
            f"""
            <div style="padding:24px;border-radius:18px;border:2px solid #000;
                        background-color:#111;color:#fff;text-align:center;">
                <div style="font-size:0.95rem;margin-bottom:10px;opacity:0.8;">
                    루빈스타인 SPE 기준 추천 최초 제시 연봉
                </div>
                <div style="font-size:2rem;font-weight:700;">
                    {format_currency(salary_star)}
                </div>
                <div style="margin-top:10px;font-size:0.95rem;opacity:0.9;">
                    (이 연봉을 처음 제시하면, 이론상 바로 수락되는 균형입니다.)
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("---")
        st.markdown("#### 균형 구조 해석")

        st.write(
            f"- 최소 수용 연봉 B: {format_currency(min_salary0)}  \n"
            f"- 회사 최대 지불 연봉 E: {format_currency(max_salary0)}"
        )
        st.write(
            f"- 근로자 몫 비율: {format_percent(share_worker)}  \n"
            f"- 회사 몫 비율: {format_percent(share_firm)}"
        )
        st.write(
            f"- 회사 입장에서는 이 연봉을 제시해도 여전히 약 "
            f"{format_currency(init_res['surplus_firm'])} 만큼의 여유 잉여가 남습니다."
        )

        with st.expander("수식 자세히 보기"):
            st.markdown(
                r"""
                **1. 파라미터**

                - 최소 수용 연봉: \( B \)  
                - 회사 최대 지불 연봉: \( E \)  
                - 구직자 할인율: \( \delta_E \)  
                - 기업 할인율: \( \delta_R \)

                **2. 루빈스타인 균형에서 근로자 몫**

                \[
                  v_W = \frac{1 - \delta_R}{1 - \delta_E \delta_R}
                \]

                이 값은 **근로자가 전체 파이 \( \pi = E - B \)** 에서 가져가는 비율입니다.

                **3. 균형 최종 연봉(=최초 제시 연봉)**

                \[
                  S^* = B + v_W \cdot (E - B)
                \]

                루빈스타인 모형에서 근로자가 먼저 제안한다고 가정하면,  
                **첫 제안이 곧바로 수락되는 균형**이므로  
                이 \( S^* \)가 바로 **추천 최초 제시 연봉**이 됩니다.
                """
            )
    else:
        st.info("나의 최소 수용 연봉 B, 회사 최대 연봉 E, 할인율 δ_E / δ_R을 입력한 뒤 계산 버튼을 눌러주세요.")


# ===================== (아래 클래스들은 건드리지 않고 그대로 둠) ====================
Actor = Literal["employee", "employer"]


@dataclass
class RoundState:
    """한 라운드의 균형 상태"""
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
            raise ValueError("B < S ≤ E 관계가 성립해야 합니다.")
        if not (0 < self.delta_e <= 1 and 0 < self.delta_r <= 1):
            raise ValueError("할인율(delta_e, delta_r)은 0과 1 사이여야 합니다.")

    @property
    def pie(self) -> float:
        """협상의 전체 파이 π = E - B"""
        return self.E - self.B

    @property
    def x_target(self) -> float:
        """최종 시점 t에서 구직자가 가져가고자 하는 파이의 비율 x."""
        return (self.S - self.B) / self.pie

    def compute_equilibrium_path(
        self,
        last_mover: Actor = "employee",
    ) -> List[RoundState]:
        """
        t 시점(라운드 index=0)의 구직자 몫을 x_target으로 놓고,
        교대로 1 - δ * 상대 몫을 적용해 t-1, t-2 ... 를 역산.
        """
        W_e = self.x_target
        W_r = 1.0 - W_e
        states: List[RoundState] = [
            RoundState(round_index=0, proposer=last_mover, W_e=W_e, W_r=W_r)
        ]

        proposer = last_mover

        for step in range(1, self.horizon + 1):
            if proposer == "employee":
                W_r_prev = 1.0 - self.delta_e * W_e
                W_e_prev = 1.0 - W_r_prev
                proposer_prev: Actor = "employer"
            else:
                W_e_prev = 1.0 - self.delta_r * W_r
                W_r_prev = 1.0 - W_e_prev
                proposer_prev = "employee"

            states.append(
                RoundState(
                    round_index=-step,
                    proposer=proposer_prev,
                    W_e=W_e_prev,
                    W_r=W_r_prev,
                )
            )

            W_e, W_r, proposer = W_e_prev, W_r_prev, proposer_prev

        states.sort(key=lambda s: s.round_index)
        return states

    def recommend_employee_offer(
        self,
        current_round_index: int,
        current_proposer: Actor,
    ) -> float:
        """
        current_round_index 기준으로, 지금 또는 다음 employee 차례의 추천 연봉.
        """
        path = self.compute_equilibrium_path(last_mover="employee")

        if current_proposer == "employee":
            candidate = max(
                (stt for stt in path if stt.round_index == current_round_index),
                key=lambda stt: stt.round_index,
            )
        else:
            candidate = max(
                (stt for stt in path
                 if stt.round_index >= current_round_index
                 and stt.proposer == "employee"),
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
        """TODO: 히스토리를 기반으로 delta_e, delta_r 업데이트 로직."""
        pass
