"""AURA Streamlit client with an authenticated in-process Python service.

Run locally with AURA_DEMO_MODE=1 to inspect the interface with real stock
data from the original AURA Supabase project and preview-only client records.
Production mode uses Supabase Auth and a server-side Python service inside Streamlit.
"""
from __future__ import annotations

import html
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st


st.set_page_config(page_title="AURA | Wealth Advisory", page_icon="✦", layout="wide", initial_sidebar_state="expanded")

ROOT = Path(__file__).resolve().parent
QUESTIONS = json.loads((ROOT / "questions.json").read_text())
DEMO = os.getenv("AURA_DEMO_MODE", "").lower() in {"1", "true", "yes"}
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
PUBLIC_URL = os.getenv("AURA_PUBLIC_URL", "").rstrip("/")
SERVER_KEY_READY = bool(os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
:root { --navy:#12243a; --teal:#007f7a; --muted:#657585; --line:#dce5e8; --paper:#ffffff; --canvas:#f5f8f7; }
html, body, [class*="css"], [data-testid="stApp"] { font-family: 'DM Sans', sans-serif; }
[data-testid="stAppViewContainer"] { background:var(--canvas); color:var(--navy); }
[data-testid="stSidebar"] { background:#10243b; }
[data-testid="stSidebar"] * { color:#edf7f6 !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { border-radius:10px; padding:4px 6px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background:#1b3b50; }
.block-container { max-width:1160px; padding-top:2.6rem; padding-bottom:4rem; }
h1,h2,h3 { font-family:'Manrope',sans-serif; letter-spacing:-.045em; color:var(--navy); }
h1 { font-weight:800; }
p { line-height:1.65; }
.aura-logo { font:800 1.55rem 'Manrope',sans-serif; letter-spacing:.15em; color:white; }
.aura-side-sub { font-size:.76rem; letter-spacing:.14em; text-transform:uppercase; color:#92b9bc; margin-bottom:2rem; }
.aura-eyebrow { font-size:.72rem; font-weight:800; letter-spacing:.18em; color:#007f7a; text-transform:uppercase; margin-bottom:.55rem; }
.aura-hero { background:linear-gradient(120deg,#12243a,#1d4e59); border-radius:22px; padding:2.6rem 3rem; color:white; margin-bottom:1.8rem; box-shadow:0 16px 38px #16384a16; }
.aura-hero h1 { color:white; margin:.25rem 0 .55rem; font-size:clamp(2rem,4vw,3.1rem); }
.aura-hero p { color:#d9e9e9; max-width:660px; margin:0; }
.aura-hero .aura-eyebrow { color:#8de3d5; }
.aura-card { background:white; border:1px solid var(--line); border-radius:18px; padding:1.35rem 1.55rem; margin:.75rem 0; box-shadow:0 6px 22px #153c4a08; }
.aura-card-title { font:700 1.04rem 'Manrope',sans-serif; color:var(--navy); margin-bottom:.25rem; }
.aura-muted { color:var(--muted); font-size:.9rem; }
.aura-kicker { color:var(--teal); font-size:.76rem; font-weight:700; text-transform:uppercase; letter-spacing:.12em; }
.aura-number { font:800 2.25rem 'Manrope',sans-serif; letter-spacing:-.05em; color:var(--teal); }
.aura-step { display:inline-block; border:1px solid #b4d8d2; color:#006d68; border-radius:100px; padding:.38rem .8rem; font-size:.8rem; font-weight:700; margin-right:.55rem; background:#ecf8f5; }
.aura-badge { display:inline-block; background:#e2f4f0; color:#056f69; padding:.32rem .65rem; border-radius:100px; font-size:.76rem; font-weight:700; }
.aura-badge-warn { background:#fff3db; color:#8b6000; }
.aura-divider { border-top:1px solid var(--line); margin:1.15rem 0; }
.aura-auth { max-width:520px; margin:2rem auto; }
div.stButton>button[kind="primary"] { background:#007f7a; border:1px solid #007f7a; border-radius:10px; font-weight:700; }
div.stButton>button { border-radius:10px; font-weight:600; min-height:42px; }
[data-testid="stMetric"] { background:white; border:1px solid var(--line); border-radius:15px; padding:1rem 1.1rem; }
[data-testid="stProgress"]>div>div { background:#007f7a; }
</style>
""", unsafe_allow_html=True)


def safe(value: object) -> str:
    return html.escape(str(value if value is not None else "—"))


def set_page(page: str):
    st.session_state.page = page
    # Sync the sidebar widget before it is rendered on the next run. Otherwise
    # its previous selection can override a page change made by a button.
    st.session_state["_nav_sync"] = page


def request_json(url: str, method: str = "GET", payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=body, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(req, timeout=25) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        try:
            error = json.loads(exc.read())
            detail = (error.get("detail") or error.get("msg") or error.get("error_description")
                      or error.get("message") or "The service could not complete this request.")
        except (ValueError, AttributeError):
            detail = "The service could not complete this request."
        if isinstance(detail, dict):
            detail = detail.get("message") or "The service could not complete this request."
        raise RuntimeError(str(detail)) from None
    except URLError:
        raise RuntimeError("The service is unavailable. Please check its address and try again.") from None


def auth_request(path: str, payload: dict, method: str = "POST", query: dict | None = None, token: str | None = None):
    url = SUPABASE_URL + "/auth/v1/" + path
    if query:
        url += "?" + urlencode(query)
    headers = {"apikey": PUBLISHABLE_KEY}
    if token:
        headers["Authorization"] = "Bearer " + token
    return request_json(url, method, payload, headers)


def session() -> dict | None:
    data = st.session_state.get("aura_session")
    if not data:
        return None
    if data.get("expires_at", 0) - time.time() > 60:
        return data
    if not data.get("refresh_token"):
        st.session_state.pop("aura_session", None)
        return None
    try:
        refreshed = auth_request("token", {"refresh_token": data["refresh_token"]}, query={"grant_type": "refresh_token"})
        save_session(refreshed)
        return st.session_state["aura_session"]
    except RuntimeError:
        st.session_state.pop("aura_session", None)
        return None


def save_session(data: dict):
    if not data.get("access_token") or not data.get("user"):
        raise RuntimeError("Please confirm your email, then sign in.")
    data["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    st.session_state["aura_session"] = data


def api(path: str, method: str = "GET", payload=None):
    auth = session()
    if not auth:
        raise RuntimeError("Please sign in again.")
    from local_api import call
    return call(path, method, payload, auth["access_token"])


def demo_profile(answers: dict):
    from aura_engine import score_client, suitability
    profile = score_client(answers)
    status, flags = suitability(answers, profile["item_scores"], profile["client_risk_category"])
    return {"analysis_id": "LOCAL-DEMO", "client_profile": profile,
            "suitability_status": status, "gate_flags": flags}


@st.cache_data(ttl=300)
def live_stocks() -> list[dict]:
    """Read the original AURA stock_metrics_excel table using a public key only."""
    if not SUPABASE_URL or not PUBLISHABLE_KEY:
        raise RuntimeError("The original AURA Supabase connection is not configured.")
    from aura_engine import prepare_stock_data
    rows = []
    offset = 0
    while True:
        query = urlencode({"select": "*", "order": "stock_code.asc",
                           "limit": 500, "offset": offset})
        batch = request_json(f"{SUPABASE_URL}/rest/v1/stock_metrics_excel?{query}",
                             headers={"apikey": PUBLISHABLE_KEY})
        if not isinstance(batch, list):
            raise RuntimeError("Supabase did not return a stock list.")
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += len(batch)
    if not rows:
        raise RuntimeError("The original AURA stock table returned no visible records.")
    return prepare_stock_data(rows, drawdown_unit="fraction").to_dict(orient="records")


@st.cache_data(ttl=3600)
def portfolio_covariance():
    from aura_engine import load_covariance_matrix
    path = ROOT / "AURA_Covariance.xlsx"
    if not path.is_file():
        raise RuntimeError("The original AURA covariance file is unavailable.")
    return load_covariance_matrix(str(path))


def live_recommendations(answers):
    import pandas as pd
    from aura_engine import construct_recommendations, run_aura
    stocks = live_stocks()
    result = run_aura(answers, pd.DataFrame(stocks))
    if result["suitability_status"] == "NOT_ELIGIBLE":
        return {"recommendations": [], "calibration": None, "source_count": len(stocks)}
    picks = construct_recommendations(result["matching_stocks"], target_n=15,
                                      max_sector_weight=.25, max_tier_weight=.50)
    if len(picks) != 15:
        raise RuntimeError("The current stock data did not produce 15 suitable candidates.")
    return {"recommendations": picks.to_dict(orient="records"),
            "calibration": result["calibration"], "source_count": len(stocks)}


def hero(eyebrow: str, title: str, description: str):
    st.markdown(f'<section class="aura-hero"><div class="aura-eyebrow">{safe(eyebrow)}</div>'
                f'<h1>{safe(title)}</h1><p>{safe(description)}</p></section>', unsafe_allow_html=True)


def badge(text: str, warn=False):
    return f'<span class="aura-badge{" aura-badge-warn" if warn else ""}">{safe(text)}</span>'


def sign_in():
    st.markdown('<div class="aura-auth"><div class="aura-eyebrow">SECURE CLIENT ACCESS</div><h1>Welcome to AURA</h1>'
                '<p class="aura-muted">Create an account or sign in to begin your investment assessment.</p></div>',
                unsafe_allow_html=True)
    if DEMO:
        st.info("DESIGN PREVIEW · Stock information is read from the original AURA Supabase project. These account forms are a preview; do not enter a real email or password.")
        tab_login, tab_signup, tab_reset = st.tabs(["Sign in", "Create account", "Reset password"])
        with tab_login:
            st.text_input("Email", placeholder="name@example.com", disabled=True, key="demo_login_email")
            st.text_input("Password", placeholder="••••••••", type="password", disabled=True, key="demo_login_password")
            if st.button("Enter sample workspace", type="primary", use_container_width=True, key="demo_enter_login"):
                st.session_state.demo_entered = True
                set_page("Home")
                st.rerun()
        with tab_signup:
            st.text_input("Email", placeholder="name@example.com", disabled=True, key="demo_signup_email")
            st.text_input("Password (at least 8 characters)", type="password", disabled=True, key="demo_signup_password")
            st.text_input("Confirm password", type="password", disabled=True, key="demo_signup_confirm")
            st.caption("In the live app, registration sends an email confirmation link.")
            if st.button("Enter sample workspace", use_container_width=True, key="demo_enter_signup"):
                st.session_state.demo_entered = True
                set_page("Home")
                st.rerun()
        with tab_reset:
            st.text_input("Account email", placeholder="name@example.com", disabled=True, key="demo_reset_email")
            st.caption("In the live app, password recovery sends an email with a reset link.")
        return
    if not (SUPABASE_URL and PUBLISHABLE_KEY and SERVER_KEY_READY):
        st.error("The client service is not configured. Add the Supabase URL, publishable key and server-only secret key before accepting registrations.")
        return
    tab_login, tab_signup, tab_reset = st.tabs(["Sign in", "Create account", "Reset password"])
    with tab_login:
        with st.form("login"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            try:
                data = auth_request("token", {"email": email.strip(), "password": password}, query={"grant_type": "password"})
                save_session(data)
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    with tab_signup:
        with st.form("signup"):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password (at least 8 characters)", type="password", key="signup_password")
            confirm = st.text_input("Confirm password", type="password", key="signup_confirm")
            submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)
        if submitted:
            if len(password) < 8 or password != confirm:
                st.error("Use at least 8 characters and make sure the passwords match.")
            else:
                try:
                    query = {"redirect_to": PUBLIC_URL} if PUBLIC_URL else None
                    data = auth_request("signup", {"email": email.strip(), "password": password}, query=query)
                    if data.get("access_token"):
                        save_session(data)
                        st.rerun()
                    st.success("Account created. Check your email for a confirmation link, then sign in.")
                except RuntimeError as exc:
                    st.error(str(exc))
    with tab_reset:
        with st.form("reset"):
            email = st.text_input("Account email", key="reset_email")
            submitted = st.form_submit_button("Send reset email", use_container_width=True)
        if submitted:
            try:
                query = {"redirect_to": PUBLIC_URL} if PUBLIC_URL else None
                auth_request("recover", {"email": email.strip()}, query=query)
                st.success("If this account exists, a password reset email has been sent.")
            except RuntimeError as exc:
                st.error(str(exc))


def recovery_flow():
    params = st.query_params
    if params.get("type") != "recovery" or not params.get("token_hash"):
        return False
    st.title("Set a new password")
    if st.session_state.get("aura_recovery_hash") != params["token_hash"]:
        try:
            data = auth_request("verify", {"token_hash": params["token_hash"], "type": "recovery"})
            save_session(data)
            st.session_state.aura_recovery_hash = params["token_hash"]
        except RuntimeError as exc:
            st.error(str(exc))
            return True
    with st.form("new_password"):
        password = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password", type="primary")
    if submitted:
        if len(password) < 8 or password != confirm:
            st.error("Use at least 8 characters and make sure the passwords match.")
        else:
            try:
                auth_request("user", {"password": password}, method="PUT", token=session()["access_token"])
                st.query_params.clear()
                st.session_state.pop("aura_recovery_hash", None)
                st.success("Password updated. You are signed in.")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    return True


def home():
    hero("AURA WEALTH ADVISORY", "Invest with a clearer view of risk.",
         "A guided journey from your circumstances and goals to a focused, risk-aware stock portfolio.")
    a, b, c = st.columns(3, gap="medium")
    for col, num, title, desc in [
        (a, "01", "Understand your profile", "Ten questions reveal your ability and willingness to take risk."),
        (b, "02", "Explore 15 candidates", "Review stocks that pass the AURA suitability and risk screens."),
        (c, "03", "Build your portfolio", "Choose 3–15 stocks; Python calculates weights and risk metrics."),
    ]:
        with col:
            st.markdown(f'<div class="aura-card"><div class="aura-number">{num}</div>'
                        f'<div class="aura-card-title">{title}</div><p class="aura-muted">{desc}</p></div>', unsafe_allow_html=True)
    st.write("")
    if st.button("Start risk assessment  →", type="primary"):
        set_page("Assessment")
        st.rerun()
    if DEMO and st.button("Jump to live stock selection preview  →"):
        try:
            answers = {q["key"]: "C" for q in QUESTIONS}
            result = demo_profile(answers)
            screen = live_recommendations(answers)
            st.session_state.answers = answers
            st.session_state.assessment = result
            st.session_state.screen = screen
            st.session_state.picks = []
            st.session_state.optimisation = None
            st.session_state.manual_optimisation = None
            st.session_state.adjustment_preview = None
            set_page("Recommendations")
            st.rerun()
        except (RuntimeError, ValueError) as exc:
            st.error(str(exc))
    st.caption("AURA is a decision support tool. Stock values can fall and outcomes are not guaranteed.")


def assessment():
    hero("STEP 1 OF 3", "Your risk assessment", "Answer ten focused questions. Your responses determine suitability before any stocks are shown.")
    step = st.session_state.setdefault("step", 0)
    q = QUESTIONS[step]
    st.progress((step + 1) / 10, text=f"Question {step+1} of 10")
    st.markdown(f'<div class="aura-eyebrow">{safe(q["section"])}</div>', unsafe_allow_html=True)
    st.subheader(q["text"])
    options = {o["key"]: o["label"] for o in q["options"]}
    current = st.session_state.setdefault("answers", {}).get(q["key"])
    selected = st.radio("Select one answer", list(options), index=list(options).index(current) if current in options else None,
                        format_func=lambda key: options[key], key=f"answer_{q['key']}", label_visibility="collapsed")
    if selected:
        st.session_state.answers[q["key"]] = selected
    with st.expander("Why we ask this"):
        st.write(q["whyItMatters"])
    left, right = st.columns([1, 4])
    with left:
        if st.button("← Previous", disabled=step == 0, use_container_width=True):
            st.session_state.step -= 1
            st.rerun()
    with right:
        label = "See my results  →" if step == 9 else "Next question  →"
        if st.button(label, type="primary", disabled=selected is None, use_container_width=True):
            if step < 9:
                st.session_state.step += 1
            else:
                try:
                    answers = st.session_state.answers
                    result = demo_profile(answers) if DEMO else api("/api/score", "POST", {"answers": answers})
                    st.session_state.assessment = result
                    st.session_state.screen = None
                    st.session_state.optimisation = None
                    st.session_state.manual_optimisation = None
                    st.session_state.adjustment_preview = None
                    st.session_state.picks = []
                    set_page("Recommendations")
                except RuntimeError as exc:
                    st.error(str(exc))
                    return
            st.rerun()


def recommendations():
    result = st.session_state.get("assessment")
    if not result:
        st.info("Complete the risk assessment to see your results.")
        if st.button("Start assessment", type="primary"):
            set_page("Assessment")
            st.rerun()
        return
    profile = result["client_profile"]
    status = result["suitability_status"]
    hero("STEP 2 OF 3", "Your investment profile", "AURA applies suitability rules before screening stocks against your risk policy.")
    a, b, c = st.columns(3)
    a.metric("Risk score", f'{profile["client_risk_score"]:.1f} / 100')
    b.metric("Risk category", profile["client_risk_category"])
    c.metric("Suitability", status.replace("_", " ").title())
    if status == "NOT_ELIGIBLE":
        st.warning("Your answers indicate that a portfolio of individual stocks may not be suitable now. No stocks will be recommended.")
        if result.get("gate_flags"):
            st.caption("Suitability checks: " + ", ".join(result["gate_flags"]))
        return
    if status == "ELIGIBLE_RESTRICTED":
        st.warning("Some answers call for tighter risk limits. The stock screen will use a restricted policy.")
    if st.session_state.get("screen") is None:
        if st.button("Find my 15 candidates  →", type="primary"):
            try:
                with st.spinner("Screening stocks against your risk profile…"):
                    if DEMO:
                        st.session_state.screen = live_recommendations(st.session_state.answers)
                    else:
                        st.session_state.screen = api("/api/screen", "POST", {"analysis_id": result["analysis_id"]})
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        return
    stocks = st.session_state.screen.get("recommendations", [])
    if len(stocks) != 15:
        st.warning("A complete set of 15 candidates is not available for this assessment.")
        return
    st.markdown("### 15 risk-matched candidates")
    st.caption("Select 3–15 stocks for portfolio optimisation. Stock data comes from the original AURA Supabase table.")
    selected = st.session_state.setdefault("picks", [])
    for i in range(0, 15, 3):
        columns = st.columns(3, gap="medium")
        for col, stock in zip(columns, stocks[i:i+3]):
            code = stock["stock_code"]
            with col:
                st.markdown(f'<div class="aura-card"><div class="aura-kicker">{safe(stock.get("sector"))}</div>'
                            f'<div class="aura-card-title">{safe(stock.get("company_name"))}</div>'
                            f'<div class="aura-muted">{safe(code)} · Risk {safe(stock.get("risk_score"))}</div></div>',
                            unsafe_allow_html=True)
                checked = st.checkbox("Add to portfolio", value=code in selected, key=f"pick_{code}",
                                      disabled=len(selected) >= 15 and code not in selected)
                if checked and code not in selected:
                    selected.append(code)
                elif not checked and code in selected:
                    selected.remove(code)
    st.write(f"**Selected: {len(selected)} / 3–15**")
    if 3 <= len(selected) <= 15:
        from aura_engine import single_stock_cap
        calibration = st.session_state.screen.get("calibration") or {}
        policy_max = calibration.get("max_single_weight", 0.55)
        cap = single_stock_cap(len(selected), policy_max)
        st.caption(f"Current single-stock ceiling: {cap:.0%} of this stock portfolio. "
                   "Your risk policy or sector limit may be stricter.")
        if len(selected) < 12:
            st.info("A small group of individual stocks remains concentrated. Consider a broader selection if it fits your goals.")
        amount_col, currency_col = st.columns([2, 1])
        amount = amount_col.number_input("Amount to invest in this stock portfolio",
                                         min_value=.01, max_value=1_000_000_000_000.0,
                                         value=10_000.0, step=1_000.0, format="%.2f",
                                         key="investment_amount")
        currency = currency_col.selectbox("Currency", ["EUR", "CNY", "USD", "GBP"],
                                          key="portfolio_currency")
        st.caption("AURA will turn the optimised weights into suggested stock amounts. This is the amount for this stock portfolio, not your total wealth.")
    if st.button("Optimise selected portfolio  →", type="primary", disabled=not 3 <= len(selected) <= 15):
        try:
            with st.spinner("Calculating risk-based weights and saving your portfolio…"):
                if DEMO:
                    import pandas as pd
                    from aura_engine import allocate_investment_amounts, optimise_selected_portfolio
                    chosen = [s for s in stocks if s["stock_code"] in selected]
                    computed = optimise_selected_portfolio(
                        pd.DataFrame(chosen), portfolio_covariance(), calibration)
                    with_amounts = allocate_investment_amounts(computed["portfolio"], amount)
                    optimisation = {
                        "portfolio": with_amounts.to_dict(orient="records"),
                        "summary": computed["summary"],
                        "analysis_id": "LOCAL-DEMO",
                        "constraints": computed["constraints"],
                        "total_amount": round(float(amount), 2),
                        "currency": currency,
                    }
                else:
                    optimisation = api("/api/optimise", "POST", {"analysis_id":result["analysis_id"],
                                                                   "selected_codes":selected,
                                                                   "total_amount":amount,
                                                                   "currency":currency})
            st.session_state.optimisation = optimisation
            st.session_state.manual_optimisation = None
            st.session_state.adjustment_preview = None
            set_page("Portfolio")
            st.rerun()
        except (RuntimeError, ValueError) as exc:
            st.error(str(exc))
            st.info("If the selected stocks cannot satisfy risk or sector limits, choose a different mix or add more stocks.")


def portfolio():
    original = st.session_state.get("optimisation")
    if not original:
        st.info("Choose 3–15 stocks from your recommendations first.")
        return
    result = st.session_state.get("manual_optimisation") or original
    hero("STEP 3 OF 3", "Your investment portfolio",
         "See Python's suggested stock amounts, then adjust an amount and compare the weights and risk before and after.")
    rows = result["portfolio"]
    if not 3 <= len(rows) <= 15 or abs(sum(float(r["weight"]) for r in rows)-1) > 1e-5:
        st.error("The portfolio result is invalid. Please try again.")
        return
    total_amount = original.get("total_amount")
    currency = original.get("currency", "EUR")
    if total_amount is not None:
        st.metric("Investment amount for these selected stocks", f'{currency} {float(total_amount):,.2f}')
        if st.button("Change total investment amount"):
            set_page("Recommendations")
            st.rerun()
        st.markdown("### Current allocation")
        st.dataframe([{"Stock": row.get("stock_code"), "Company": row.get("company_name"),
                       "Weight": f'{float(row["weight"])*100:.2f}%',
                       f"Amount ({currency})": f'{float(row.get("investment_amount",0)):,.2f}'}
                      for row in rows], hide_index=True, use_container_width=True)
        if abs(sum(float(row.get("investment_amount", 0)) for row in rows) - float(total_amount)) > .011:
            st.error("The displayed stock amounts do not add up to the investment amount.")
            return
    else:
        for row in rows:
            st.write(f'{row.get("stock_code")} · {row.get("company_name")}: {float(row["weight"])*100:.2f}%')
    metrics = result.get("summary", {})
    a,b,c,d = st.columns(4)
    a.metric("Portfolio volatility", f'{float(metrics.get("portfolio_volatility",0))*100:.1f}%')
    b.metric("5Y adjusted beta", f'{float(metrics.get("portfolio_beta_5y",0)):.2f}')
    c.metric("3Y adjusted beta", f'{float(metrics.get("portfolio_beta_3y",0)):.2f}')
    d.metric("Single-stock cap", f'{float(result.get("constraints",{}).get("max_single_weight",0))*100:.0f}%')
    st.caption("Portfolio percentages refer to the selected stocks and do not imply an overall equity allocation. "
               "The concentration cap is an AURA design setting, not a universal regulatory limit.")
    if total_amount is not None:
        st.markdown("### Adjust an individual stock amount")
        st.caption("The total stays fixed. When you change one stock, AURA reallocates the remainder across the other selected stocks in proportion to their current weights and checks every risk limit.")
        codes = [row["stock_code"] for row in rows]
        by_code = {row["stock_code"]: row for row in rows}
        stock_code = st.selectbox("Stock to adjust", codes,
                                  format_func=lambda code: f'{code} · {by_code[code].get("company_name", "")}',
                                  key="adjust_stock_code")
        current_amount = float(by_code[stock_code].get("investment_amount", 0))
        new_amount = st.number_input(f"Your preferred amount ({currency})", min_value=.01,
                                     max_value=max(.01, float(total_amount)-.01),
                                     value=max(.01, current_amount), step=100.0,
                                     format="%.2f", key=f"adjust_amount_{stock_code}")
        if st.button("Preview and apply amount change  →", type="primary"):
            try:
                if DEMO:
                    import pandas as pd
                    from aura_engine import preview_amount_adjustment
                    preview = preview_amount_adjustment(
                        {"portfolio": pd.DataFrame(rows), "constraints": result["constraints"]},
                        portfolio_covariance(), float(total_amount), stock_code, float(new_amount))
                    response = {"original_portfolio": original["portfolio"],
                                "original_summary": original["summary"],
                                "adjusted_portfolio": preview["portfolio"].to_dict(orient="records"),
                                "adjusted_summary": preview["summary"],
                                "checks": preview["checks"].to_dict(orient="records"),
                                "valid": preview["valid"], "saved": preview["valid"],
                                "total_amount": float(total_amount)}
                else:
                    response = api("/api/portfolio/adjust", "POST", {
                        "analysis_id": original["analysis_id"],
                        "stock_code": stock_code, "new_amount": float(new_amount)})
                st.session_state.adjustment_preview = response
                if response["saved"]:
                    st.session_state.manual_optimisation = {
                        **original, "portfolio": response["adjusted_portfolio"],
                        "summary": response["adjusted_summary"]}
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        comparison = st.session_state.get("adjustment_preview")
        if comparison:
            if comparison["valid"]:
                st.success("Adjustment passed all risk checks and is saved for this analysis." if not DEMO
                           else "Adjustment passed the sample risk checks. Preview changes are not saved online.")
            else:
                failed = [check["check"] for check in comparison.get("checks", []) if not check.get("passed")]
                st.error("This amount would break a risk limit, so the current portfolio was not changed.")
                if failed:
                    st.caption("Limits exceeded: " + ", ".join(failed))
            st.markdown("### Before and after")
            before = {row["stock_code"]: row for row in comparison["original_portfolio"]}
            after = {row["stock_code"]: row for row in comparison["adjusted_portfolio"]}
            st.dataframe([{"Stock": code,
                           f"Before ({currency})": f'{float(before[code].get("investment_amount",0)):,.2f}',
                           f"After ({currency})": f'{float(after[code].get("investment_amount",0)):,.2f}',
                           f"Change ({currency})": f'{float(after[code].get("investment_amount",0))-float(before[code].get("investment_amount",0)):+,.2f}',
                           "Before weight": f'{float(before[code]["weight"])*100:.2f}%',
                           "After weight": f'{float(after[code]["weight"])*100:.2f}%'}
                          for code in before], hide_index=True, use_container_width=True)
            old = comparison["original_summary"] or {}
            new = comparison["adjusted_summary"] or {}
            st.dataframe([{"Risk metric": label, "Before": display(old.get(key)),
                           "After": display(new.get(key))}
                          for label, key, display in [
                              ("Annualised volatility", "portfolio_volatility", lambda x: f'{float(x or 0)*100:.2f}%'),
                              ("5Y adjusted beta", "portfolio_beta_5y", lambda x: f'{float(x or 0):.2f}'),
                              ("3Y adjusted beta", "portfolio_beta_3y", lambda x: f'{float(x or 0):.2f}')]],
                         hide_index=True, use_container_width=True)
        if st.session_state.get("manual_optimisation") and st.button("Restore optimised amounts"):
            try:
                if not DEMO:
                    api("/api/portfolio/reset", "POST", {"analysis_id": original["analysis_id"]})
                st.session_state.manual_optimisation = None
                st.session_state.adjustment_preview = None
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    if DEMO:
        st.info("Preview only: stock data comes from Supabase and covariance from the original AURA file. No client record or PDF is saved online.")
    elif st.button("Request Gemini explanation report  →", type="primary"):
        try:
            api("/api/reports", "POST", {"analysis_id":result["analysis_id"]})
            set_page("My Reports")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))


def history():
    hero("YOUR RECORDS", "My Investment History", "Every analysis keeps its own assessment, stock snapshot and portfolio result.")
    if DEMO:
        st.info("LOCAL PREVIEW · Only the current analysis from this browser session appears here. Sign in to the configured live service to save customer history.")
        entries = []
        profile = st.session_state.get("assessment")
        baseline = st.session_state.get("optimisation")
        if profile:
            current = (st.session_state.get("manual_optimisation") or baseline) if baseline else None
            entries.append({
                "assessment": {"analysis_id": "LOCAL-DEMO",
                               "created_at": datetime.now().isoformat(timespec="minutes"),
                               "risk_score": profile["client_profile"]["client_risk_score"],
                               "risk_category": profile["client_profile"]["client_risk_category"],
                               "suitability_status": profile["suitability_status"].lower()},
                "recommendations": {"stocks_snapshot": st.session_state.get("screen", {}).get("recommendations", [])},
                "portfolio": {"positions": current["portfolio"],
                              "risk_metrics": current["summary"],
                              "optimization_inputs": {
                                  "total_amount": baseline.get("total_amount"),
                                  "currency": baseline.get("currency", "EUR"),
                                  "mode": "manual" if st.session_state.get("manual_optimisation") else "optimised",
                                  "baseline_positions": baseline["portfolio"],
                                  "baseline_risk_metrics": baseline["summary"]}} if current else None})
    else:
        try:
            with st.spinner("Loading your investment history…"):
                entries = api("/api/history")
        except RuntimeError as exc:
            st.error(str(exc))
            return
    if not entries:
        st.info("No analyses yet. Start with the risk assessment.")
    for index, item in enumerate(entries):
        a = item["assessment"]
        title = f'{a.get("risk_category", "Assessment")} · {a.get("created_at", "")[:10]}'
        with st.expander(title, expanded=DEMO and index == 0):
            x,y,z = st.columns(3)
            x.metric("Risk score", f'{float(a["risk_score"]):.1f} / 100')
            y.metric("Suitability", a.get("suitability_status", "—").replace("_"," ").title())
            z.metric("Analysis", a["analysis_id"][:8])
            rec = item.get("recommendations")
            port = item.get("portfolio")
            st.write("**Recommended stocks**")
            if rec:
                st.dataframe([{"Code":s.get("stock_code"),"Company":s.get("company_name"),
                               "Sector":s.get("sector"),"Risk score":s.get("risk_score")}
                              for s in rec.get("stocks_snapshot",[])], hide_index=True, use_container_width=True)
            else:
                st.caption("No recommendation snapshot for this assessment.")
            st.write("**Selected portfolio**")
            if port:
                inputs = port.get("optimization_inputs") or {}
                currency = inputs.get("currency", "EUR")
                if inputs.get("total_amount"):
                    st.metric("Investment amount for selected stocks",
                              f'{currency} {float(inputs["total_amount"]):,.2f}')
                st.dataframe([{"Code":s.get("stock_code"),"Company":s.get("company_name"),
                               "Weight":f'{float(s.get("weight",0))*100:.1f}%',
                               f"Amount ({currency})":f'{float(s.get("investment_amount",0)):,.2f}'
                               if inputs.get("total_amount") else "—"}
                              for s in port.get("positions",[])], hide_index=True, use_container_width=True)
                m = port.get("risk_metrics",{})
                st.caption(f'Volatility {float(m.get("portfolio_volatility",0))*100:.1f}% · '
                           f'Beta 5Y {float(m.get("portfolio_beta_5y",0)):.2f} · '
                           f'Beta 3Y {float(m.get("portfolio_beta_3y",0)):.2f}')
                if inputs.get("mode") == "manual" and inputs.get("baseline_positions"):
                    st.write("**Original recommendation vs personalised amounts**")
                    initial = {s["stock_code"]: s for s in inputs["baseline_positions"]}
                    st.dataframe([{"Code":s["stock_code"],
                                   f"Original ({currency})":f'{float(initial[s["stock_code"]].get("investment_amount",0)):,.2f}',
                                   f"Personalised ({currency})":f'{float(s.get("investment_amount",0)):,.2f}',
                                   "Original weight":f'{float(initial[s["stock_code"]]["weight"])*100:.2f}%',
                                   "Personalised weight":f'{float(s["weight"])*100:.2f}%'}
                                  for s in port["positions"]], hide_index=True, use_container_width=True)
            else:
                st.caption("No portfolio saved for this assessment.")
            if port and not DEMO and st.button("Request or view Gemini report", key=f'queue_history_{a["analysis_id"]}'):
                try:
                    api("/api/reports", "POST", {"analysis_id": a["analysis_id"]})
                    set_page("My Reports")
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))


@st.fragment(run_every="15s")
def report_status_list():
    if st.button("Refresh statuses"):
        st.rerun(scope="fragment")
    st.caption("Statuses refresh automatically every 15 seconds. Download links expire after 60 seconds.")
    try:
        rows = api("/api/reports")
    except RuntimeError as exc:
        st.error(str(exc))
        return
    if not rows:
        st.info("No reports yet. Optimise a portfolio to request one.")
    for row in rows:
        status = str(row.get("status", "pending")).lower()
        label = status.title()
        descriptions = {"pending": "Waiting for report generation",
                        "processing": "Generating the report",
                        "completed": "PDF ready",
                        "failed": "Report generation did not complete"}
        if status == "failed" and row.get("error_code") == "worker_interrupted":
            descriptions["failed"] = "Report generation was interrupted. You can retry it."
        st.markdown(f'<div class="aura-card"><div class="aura-card-title">Report {safe(row.get("report_id", "")[:8])} '
                    f'{badge(label, warn=status=="failed")}</div><div class="aura-muted">Created {safe(row.get("created_at", "")[:16])} · '
                    f'Analysis {safe(row.get("analysis_id", "")[:8])}</div>'
                    f'<p>{safe(descriptions.get(status,"Status unavailable"))}</p></div>', unsafe_allow_html=True)
        if status == "completed":
            if st.button("Get private PDF link", key=f'download_{row["report_id"]}'):
                try:
                    data = api(f'/api/reports/{row["report_id"]}/download')
                    st.link_button("Download PDF (expires in 60 seconds)", data["url"])
                except RuntimeError as exc:
                    st.error(str(exc))
            if st.button("Regenerate with latest report template", key=f'regenerate_{row["report_id"]}'):
                try:
                    api(f'/api/reports/{row["report_id"]}/retry', "POST")
                    st.rerun(scope="fragment")
                except RuntimeError as exc:
                    st.error(str(exc))
        elif status == "failed":
            if st.button("Retry report", key=f'retry_{row["report_id"]}'):
                try:
                    api(f'/api/reports/{row["report_id"]}/retry', "POST")
                    st.rerun(scope="fragment")
                except RuntimeError as exc:
                    st.error(str(exc))


def reports():
    hero("YOUR DOCUMENTS", "My Reports", "Track Gemini explanation reports and download completed private PDFs.")
    if DEMO:
        st.info("LOCAL PREVIEW · No report is generated or saved here. Live reports require a signed-in account and the Supabase report worker.")
        return
    report_status_list()


def main():
    if not DEMO and recovery_flow():
        return
    if (DEMO and not st.session_state.get("demo_entered")) or (not DEMO and not session()):
        sign_in()
        return
    with st.sidebar:
        st.markdown('<div class="aura-logo">✦ AURA</div><div class="aura-side-sub">Wealth Advisory</div>', unsafe_allow_html=True)
        if DEMO:
            st.warning("DESIGN PREVIEW · LIVE STOCK DATA")
        else:
            st.caption(session().get("user", {}).get("email", "Signed in"))
        pages = ["Home", "Assessment", "Recommendations", "Portfolio", "My Investment History", "My Reports"]
        current = st.session_state.get("page", "Home")
        sync_page = st.session_state.pop("_nav_sync", None)
        if sync_page in pages:
            st.session_state["navigation"] = sync_page
        page = st.radio("Navigation", pages, index=pages.index(current) if current in pages else 0,
                        key="navigation")
        if page != current:
            st.session_state.page = page
            st.rerun()
        st.write("")
        if st.button("Exit preview" if DEMO else "Sign out", use_container_width=True):
            if not DEMO:
                try:
                    auth_request("logout", {}, token=session()["access_token"])
                except RuntimeError:
                    pass
            st.session_state.clear()
            st.rerun()
    page = st.session_state.get("page", "Home")
    {"Home":home,"Assessment":assessment,"Recommendations":recommendations,
     "Portfolio":portfolio,"My Investment History":history,"My Reports":reports}[page]()


if __name__ == "__main__":
    main()
