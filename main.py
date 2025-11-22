import math
import requests
import streamlit as st

from dataclasses import dataclass, field
from typing import Optional, Dict, List


# ===================== 기본 설정 =====================
st.set_page_config(
    page_title="피이직대학 이직 상담소",
    page_icon="📈",
    layout="centered",
)

API_BASE = "https://black-bread-33be.dlspike520.workers.dev/"

# 산업별 평균 연봉 상승률
INDUSTRY_GROWTH: Dict[str, float] = {
    "서비스업": 0.011,      # 1.1%
    "제조·화학업": 0.03,    # 3.0%
    "판매·유통업": 0.043,   # 4.3%
    "의료·제약업": 0.027,   # 2.7%
    "IT·통신업": 0.043      # 4.3%
}
INDUSTRY_OPTIONS = list(INDUSTRY_GROWTH.keys())


# ===================== NegotiationModel 정의 (네가 준 코드 최대한 그대로) =====================

# 직종별 고용주 최대 지불 의사 연봉 E 조회 테이블 (이 모형은 단위만 맞으면 됨)
DEFAULT_E_BY_FIELD: Dict[str, float] = {
    "it_dev": 9000.0,
    "medical": 12000.0,
    "driver": 6000.0,
    "service": 5000.0,
    "manufacturing": 7000.0,
    # ... 필요하면 계속 추가
}


@dataclass
class NegotiationState:
    # 고정 파라미터
    S_target: float          # 목표 최종 연봉 S
    B: float                 # 최소 허용 연봉 B
    E_max: float             # 고용주 최대 연봉 E (field에서 가져옴)
    field_name: str          # 직종 이름
    first_mover: str         # 'employee' or 'employer'
    total_rounds: int        # 남은 총 라운드 수 (보통 3 또는 4)

    # 할인율 (계속 업데이트될 수 있음)
    delta_E: float = 0.95    # 구직자 할인율
    delta_R: float = 0.95    # 고용주 할인율
    delta_E_hat: float = 0.95  # 고용주가 생각하는 구직자 할인율(추정치)

    # 진행 중 상태
    current_round: int = 1
    history_employee: List[float] = field(default_factory=list)
    history_employer: List[float] = field(default_factory=list)

    def remaining_rounds(self) -> int:
        return max(self.total_rounds - self.current_round + 1, 0)

    @property
    def pi(self) -> float:
        return self.E_max - self.B

    def target_share(self) -> float:
        """파이에서 구직자가 가져가고 싶은 비율 x = (S-B)/π."""
        if self.pi <= 0:
            raise ValueError("E_max must be greater than B")
        return (self.S_target - self.B) / self.pi


class NegotiationModel:
    """
    실시간 연봉 협상 모델 (게임이론 아이디어 + 휴리스틱).
    - 상태(state)를 들고 있다가
    - employer 오퍼가 들어오면 delta들을 업데이트하고
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
    ):
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

    # ------------------------------------------------------------------
    # 1) 고용주 오퍼 관찰 -> 상태 & 할인율 업데이트
    # ------------------------------------------------------------------
    def observe_employer_offer(self, offer: float) -> None:
        """
        고용주가 새 오퍼를 했을 때 호출.
        - 히스토리에 기록
        - 델타_R, delta_E_hat 갱신 (휴리스틱)
        """
        s = self.state
        s.history_employer.append(offer)

        # 고용주가 얼마나 "빨리" 타겟 S에 가까이 와 있는지로 delta_R 업데이트
        #   ratio_to_target: B~S 사이에서 지금 위치 비율
        denom = max(s.S_target - s.B, 1e-9)
        ratio_to_target = (offer - s.B) / denom
        ratio_to_target = max(0.0, min(ratio_to_target, 1.5))  # 약간 여유

        # generous(타겟에 가까움) 하면 덜 인내심 있음 -> delta_R 낮춤
        # 조금씩만 움직이게 EMA(지수이동평균) 스타일로 업데이트
        target_delta_R = 1.0 - 0.5 * min(ratio_to_target, 1.0)
        s.delta_R = 0.7 * s.delta_R + 0.3 * target_delta_R

        # 고용주가 보는 delta_E_hat도 비슷하게 조정 (약한 모델)
        # 구직자가 아직 많이 양보 안 했는데도 고용주가 크게 올려주면,
        # "아, 상대가 좀 급하다고 생각하나?" 식으로 delta_E_hat 살짝 낮춰 줌.
        target_delta_E_hat = 1.0 - 0.3 * min(ratio_to_target, 1.0)
        s.delta_E_hat = 0.8 * s.delta_E_hat + 0.2 * target_delta_E_hat

    # ------------------------------------------------------------------
    # 2) 지금 턴이 누구인지 판단
    # ------------------------------------------------------------------
    def current_player(self) -> str:
        """
        현재 라운드에서 제안해야 하는 플레이어 ('employee' or 'employer').
        first_mover 기준으로 라운드를 번갈아 가며 결정한다.
        """
        s = self.state
        if s.first_mover == "employee":
            # 1,3,5,... employee / 2,4,6,... employer
            return "employee" if s.current_round % 2 == 1 else "employer"
        else:
            # 1,3,5,... employer / 2,4,6,... employee
            return "employer" if s.current_round % 2 == 1 else "employee"

    # ------------------------------------------------------------------
    # 3) employee 턴일 때, 지금 얼마를 제안할지 계산
    # ------------------------------------------------------------------
    def _suggest_employee_offer(self) -> float:
        """
        구직자의 현재 라운드 제안값을 계산.
        - S_target, B, E_max, delta_E, 남은 라운드 수,
          마지막 고용주 오퍼 등을 이용해
        - '타겟 S를 향해 얼마나 다가갈지(step)를 결정하는' 모델
        """
        s = self.state

        remaining = s.remaining_rounds()
        if remaining <= 0:
            # 이론상 더 이상 턴이 없으면, 그냥 현재 타겟 S를 리턴
            return s.S_target

        # 마지막 고용주 오퍼 (없으면 B 기준에서 시작)
        last_emp_offer = s.history_employer[-1] if s.history_employer else s.B

        # 타겟까지 남은 거리
        gap_to_target = s.S_target - last_emp_offer

        # 구직자 인내심: delta_E가 낮을수록 급함
        urgency = 1.0 - s.delta_E  # (0~1 사이: 1에 가까울수록 급함)

        # 남은 라운드가 적을수록 더 크게 움직이도록
        round_factor = 1.0 / remaining

        # 두 요인을 섞어서 '이번에 gap의 몇 %를 움직일지' 결정
        #  - 최소 10%, 최대 90% 사이로 클램프
        step_ratio = 0.5 * urgency + 0.5 * round_factor
        step_ratio = max(0.1, min(step_ratio, 0.9))

        offer = last_emp_offer + step_ratio * gap_to_target

        # B~E_max 사이로 클램프
        offer = max(s.B, min(offer, s.E_max))

        return offer

    # ------------------------------------------------------------------
    # 4) 한 턴 진행: (필요하면 employer 오퍼 먼저 넣고) 내 제안 계산
    # ------------------------------------------------------------------
    def next_employee_offer(self, employer_offer: Optional[float] = None) -> float:
        """
        "현실"에서:
        - 만약 이번 라운드가 employer 턴이라면 employer_offer를 먼저 observe.
        - 그 다음 employee 턴이 되면 이 함수를 호출해서
          '지금 내가 얼마를 제안해야 하는지'를 얻는 용도로 쓸 수 있다.

        여기서는 단순화를 위해:
        - 현재 라운드에 고용주 오퍼가 있으면 먼저 반영하고
        - state.current_round를 employee 턴으로 맞춘 뒤
        - employee 제안을 계산해서 반환.
        """
        s = self.state

        # 1) employer 오퍼가 들어왔다면 반영
        if employer_offer is not None:
            self.observe_employer_offer(employer_offer)

        # 2) current_round를 employee 턴이 될 때까지 증가시킴
        #    (라운드가 초기에 employer에서 시작할 수도 있으므로)
        while self.current_player() != "employee" and s.current_round <= s.total_rounds:
            s.current_round += 1

        if s.current_round > s.total_rounds:
            # 더 이상 제안할 라운드가 없으면 타겟 S를 그대로 리턴
            return s.S_target

        # 3) employee 제안 계산
        offer = self._suggest_employee_offer()
        s.history_employee.append(offer)

        # 4) 이 라운드 사용 완료 -> 다음 라운드로
        s.current_round += 1

        return offer

    # ------------------------------------------------------------------
    # 5) 디버깅/로그용: 현재 상태 요약
    # ------------------------------------------------------------------
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


# ===================== 공통 유틸 (이직 여부 계산용) =====================

def fetch_corp_metrics(name: str) -> dict:
    """회사 데이터를 API에서 안전하게 가져오는 함수."""
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
    Wp/Wk 계산:
    - 현재/이직 업종 성장률을 각각 반영
    - 회사 데이터는 가능하면 DART 사용, 없으면 업종 평균/기본값 사용
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

    # 6) 숫자 기준으로만 의사결정
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
        "sp_base_now": sp_base_now,
        "sp_base_next": sp_base_next,
        "factor_now": factor_now,
        "factor_next": factor_next,
    }


# ===================== 세션 상태 초기화 =====================
if "page" not in st.session_state:
    # p2: 이직 여부 결정, p3: 연봉협상 메뉴, p4: 협상 시뮬레이터
    st.session_state["page"] = "p2"

if "jc_result" not in st.session_state:
    st.session_state["jc_result"] = None

if "neg_model" not in st.session_state:
    st.session_state["neg_model"] = None


# ===================== 공통 헤더 =====================
st.title("피이직대학 이직 상담소")

page = st.session_state["page"]
if page == "p2":
    st.subheader("- 이직 여부 결정")
elif page == "p3":
    st.subheader("- 연봉협상 메뉴")
elif page == "p4":
    st.subheader("- 협상 시뮬레이터 (게임이론 기반)")

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
                st.experimental_rerun()
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
        else:
            st.write("아직 계산된 결과가 없습니다.")


# ===================== PAGE 3: 연봉협상 메뉴 =====================
elif page == "p3":
    if st.button("뒤로 (이직 여부 결정으로)", key="back_to_p2"):
        st.session_state["page"] = "p2"
        st.experimental_rerun()

    st.markdown("### 연봉협상 메뉴")
    st.markdown(
        """
        - PAGE 2에서 이직 여부를 먼저 계산합니다.  
        - 여기서는 게임이론 기반 협상 시뮬레이터(PAGE 4)로 이동할 수 있습니다.
        """
    )

    st.markdown("---")
    if st.button("협상 시뮬레이터로 이동 (PAGE 4)", key="go_p4"):
        st.session_state["page"] = "p4"
        st.experimental_rerun()


# ===================== PAGE 4: 협상 시뮬레이터 (네 NegotiationModel 기반) =====================
elif page == "p4":
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p4"):
        st.session_state["page"] = "p3"
        st.experimental_rerun()

    st.markdown("### 협상 시뮬레이터 (게임이론 + 휴리스틱)")
    st.caption(
        "S(목표 최종 연봉), B(최소 수용 연봉), 직종(→E_max), 첫 제안자, 라운드 수를 입력하면\n"
        "고용주 오퍼가 들어올 때마다, 지금 라운드에서 구직자가 얼마를 제안해야 하는지 추천해 줍니다."
    )

    # ----- 1. 초기 세팅 폼 -----
    with st.form("neg_init_form"):
        col1, col2 = st.columns(2)
        with col1:
            S_target = st.number_input(
                "목표 최종 연봉 S (단위: 동일 단위, 예: 만 단위)",
                min_value=0.0,
                max_value=1_000_000.0,
                value=70_00.0,
                step=10.0,
            )
            B = st.number_input(
                "최소 수용 연봉 B",
                min_value=0.0,
                max_value=1_000_000.0,
                value=50_00.0,
                step=10.0,
            )
            total_rounds = st.number_input(
                "총 라운드 수 (보통 3~4)",
                min_value=1,
                max_value=10,
                value=4,
                step=1,
            )
        with col2:
            field_name = st.selectbox(
                "직종 (E_max를 결정)",
                list(DEFAULT_E_BY_FIELD.keys()),
                index=0,
            )
            first_mover = st.selectbox(
                "첫 제안자",
                ["employee", "employer"],
                format_func=lambda x: "구직자(employee)" if x == "employee" else "고용주(employer)",
            )
            delta_E = st.slider("구직자 할인율 δ_E", 0.5, 0.99, 0.95, step=0.01)
            delta_R = st.slider("고용주 할인율 δ_R", 0.5, 0.99, 0.95, step=0.01)

        submitted = st.form_submit_button("새 협상 세션 시작")

    if submitted:
        try:
            model = NegotiationModel(
                S=S_target,
                B=B,
                field_name=field_name,
                first_mover=first_mover,
                total_rounds=int(total_rounds),
                delta_E_default=delta_E,
                delta_R_default=delta_R,
            )
            st.session_state["neg_model"] = model
            st.success("✅ 새 협상 세션이 초기화되었습니다.")
        except Exception as e:
            st.session_state["neg_model"] = None
            st.error(f"협상 모델 초기화 중 오류가 발생했습니다: {e}")

    model: Optional[NegotiationModel] = st.session_state.get("neg_model")

    # ----- 2. 협상 진행 UI -----
    if model is None:
        st.info("위에서 S, B, 직종, 첫 제안자, 라운드 수를 설정한 뒤 '새 협상 세션 시작'을 눌러 주세요.")
    else:
        s = model.state
        st.markdown("---")
        st.markdown("#### 현재 상태 요약")
        st.code(model.summary())

        st.markdown("#### 이번 라운드 고용주 오퍼 입력")
        st.caption(
            "이번 라운드에 고용주가 얼마를 제안했는지 입력하고 '구직자 제안 계산'을 누르면,\n"
            "모델이 이번 employee 턴에서 제안해야 할 금액을 계산합니다.\n"
            "※ 첫 제안자가 구직자이면, 고용주 오퍼를 비워 두고 버튼을 눌러도 됩니다."
        )

        employer_offer_input = st.number_input(
            "고용주 오퍼 (없으면 0 입력 또는 그대로 두기)",
            min_value=0.0,
            max_value=1_000_000.0,
            value=0.0,
            step=10.0,
        )

        if st.button("이번 라운드 구직자 제안 계산"):
            offer_arg: Optional[float]
            if employer_offer_input > 0:
                offer_arg = employer_offer_input
            else:
                offer_arg = None

            try:
                emp_offer = model.next_employee_offer(offer_arg)
                st.success(f"이번 라운드에서 구직자가 제안해야 할 추천 연봉: **{emp_offer:.2f}**")
                st.markdown("##### 업데이트된 상태")
                st.code(model.summary())
            except Exception as e:
                st.error(f"제안 계산 중 오류가 발생했습니다: {e}")

        if st.button("🔄 협상 세션 리셋"):
            st.session_state["neg_model"] = None
            st.experimental_rerun()
