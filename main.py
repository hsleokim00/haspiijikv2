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

# 직종별 고용주 최대 지불 의사 연봉 E_max (예시용; PAGE 4에서는 직접 숫자로 넣어서 사용)
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
    # p2: 이직 여부 결정, p3: 연봉협상 메뉴, p4: 협상 시뮬레이터, p5/p6: 초기 연봉 제시
    st.session_state["page"] = "p2"

if "jc_result" not in st.session_state:
    st.session_state["jc_result"] = None

if "neg_model" not in st.session_state:
    st.session_state["neg_model"] = None

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
elif page == "p4":
    st.subheader("- 협상 시뮬레이터")
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
            <h3>협상 시뮬레이터</h3>
            <p>회사 제안 → 나의 응답을 라운드별로 돌려보며 협상을 연습합니다.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("들어가기", key="go_p4"):
            st.session_state["page"] = "p4"
            st.rerun()

    with col2:
        st.markdown(
            """<div style="padding:16px;border-radius:16px;border:1px solid #ddd;">
            <h3>(예비) 초기 연봉 제시</h3>
            <p>향후 SPE 기반·역진행 기반 최초 제시 연봉 계산 메뉴 자리.</p>
            </div>""",
            unsafe_allow_html=True,
        )
        # 나중에 page == "p5"/"p6"으로 연결 예정


# ===================== PAGE 4: 협상 시뮬레이터 (네 NegotiationModel 기반) =====================
elif page == "p4":
    if st.button("뒤로 (연봉협상 메뉴로)", key="back_to_p3_from_p4"):
        st.session_state["page"] = "p3"
        st.rerun()

    st.markdown("### 협상 시뮬레이터 (게임이론 + 휴리스틱)")
    st.caption(
        "루빈스타인 모형에서 출발한 할인율(δ) 아이디어와\n"
        "네가 설계한 목표 연봉 S, 최소 수용 연봉 B, 직종별 최대 연봉 E_max를 바탕으로\n"
        "라운드별로 '지금 얼마를 제안하면 좋을지'를 계산해 주는 시뮬레이터입니다."
    )

    # 1) 세션에서 모델 꺼내오기
    neg_model: Optional[NegotiationModel] = st.session_state.get("neg_model")

    # 2) 초기 설정 폼 (모델이 아직 없을 때는 열려 있게)
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
                total_rounds = st.number_input(
                    "전체 라운드 수 (왕복 교대 제안 횟수)",
                    min_value=1,
                    max_value=10,
                    value=4,
                    step=1,
                )
            with col2:
                field_name = st.selectbox(
                    "직종 (E_max 테이블 키)",
                    options=list(DEFAULT_E_BY_FIELD.keys()),
                    index=0,
                )
                first_mover = st.selectbox(
                    "첫 제안자",
                    options=["employee", "employer"],
                    format_func=lambda x: "구직자(employee)" if x == "employee" else "회사(employer)",
                )
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

            submitted = st.form_submit_button("새 협상 세션 시작")

        if submitted:
            try:
                model = NegotiationModel(
                    S=S_target,
                    B=B,
                    field_name=field_name,
                    first_mover=first_mover,
                    total_rounds=int(total_rounds),
                    E_table=DEFAULT_E_BY_FIELD,
                    delta_E_default=delta_E_default,
                    delta_R_default=delta_R_default,
                )
                st.session_state["neg_model"] = model
                neg_model = model
                st.success("✅ 새 협상 세션이 초기화되었습니다.")
            except Exception as e:
                st.error(f"협상 모델 초기화 중 오류가 발생했습니다: {e}")

    # 3) 모델이 아직 없으면 안내 후 종료
    if neg_model is None:
        st.info("위에서 협상 기본 설정을 마친 뒤, 새 협상 세션을 시작해 주세요.")
        st.stop()

    # 4) 현재 상태 요약 보여주기
    st.markdown("#### 현재 협상 상태")
    st.code(neg_model.summary(), language="text")

    st.markdown("---")

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
                help="회사 오퍼가 없다면 0으로 두고, 바로 내 제안을 계산할 수도 있습니다.",
            )
            has_employer_offer = st.checkbox(
                "이번 라운드에 회사 오퍼가 있었다",
                value=True,
            )
        with col2:
            run_step = st.form_submit_button("내 추천 제안 계산하기")

    if run_step:
        try:
            if not has_employer_offer:
                employer_offer_val: Optional[float] = None
            else:
                employer_offer_val = employer_offer

            suggested = neg_model.next_employee_offer(
                employer_offer=employer_offer_val
            )

            st.success(
                f"💡 이번 라운드에서 추천되는 나의 제안 연봉: **{suggested:,.0f} 만원**"
            )
            st.markdown(
                f"- 현재 라운드: **{neg_model.state.current_round - 1} / {neg_model.state.total_rounds}**  \n"
                f"- 지금 턴 이후 남은 라운드 수: **{neg_model.state.remaining_rounds()}**  \n"
                f"- 최근 회사 오퍼 히스토리: `{neg_model.state.history_employer}`  \n"
                f"- 나의 과거 제안 히스토리: `{neg_model.state.history_employee}`"
            )
        except Exception as e:
            st.error(f"제안 계산 중 오류가 발생했습니다: {e}")

    # 6) 세션 리셋 버튼
    if st.button("🔄 협상 세션 리셋"):
        st.session_state["neg_model"] = None
        st.rerun()


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
