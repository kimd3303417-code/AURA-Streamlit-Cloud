COLUMN_ALIASES = {'stock_code': 'stock_code', 'company_name': 'company_name', 'Sector': 'sector', 'sector': 'sector', 'Tier': 'tier', 'tier': 'tier', 'RISK SCORE': 'risk_score', 'risk_score': 'risk_score', 'RETURN SCORE': 'return_score', 'return_score': 'return_score', '3Y max drawdown': 'max_drawdown_3y', 'max_drawdown_3y': 'max_drawdown_3y', '3Y percentile': 'percentile_3y', 'percentile_3y': 'percentile_3y', '5Y percentile': 'percentile_5y', 'percentile_5y': 'percentile_5y', 'volatility_3y_annualised': 'volatility_3y_annualised', '5Y adjusted beta': 'adjusted_beta_5y', 'adjusted_beta_5y': 'adjusted_beta_5y', '3Y adjusted beta': 'adjusted_beta_3y', 'adjusted_beta_3y': 'adjusted_beta_3y', 'return_3y_pa': 'return_3y_pa'}

CATEGORY_CUTS = [25, 45, 65, 83]

import numpy as np
import pandas as pd
from scipy.optimize import milp, minimize, LinearConstraint, Bounds

ITEM_SCORES = {'Q1': {'A': 0, 'B': 35, 'C': 70, 'D': 100}, 'Q2': {'A': 0, 'B': 30, 'C': 70, 'D': 100}, 'Q3': {'A': 0, 'B': 25, 'C': 55, 'D': 85, 'E': 100}, 'Q4': {'A': 0, 'B': 35, 'C': 70, 'D': 100}, 'Q5': {'A': 0, 'B': 25, 'C': 50, 'D': 75, 'E': 100}, 'Q6': {'A': 0, 'B': 25, 'C': 50, 'D': 75, 'E': 100}, 'Q7': {'A': 0, 'B': 30, 'C': 65, 'D': 100}, 'Q8': {'A': 0, 'B': 30, 'C': 65, 'D': 100}, 'Q9': {'A': 0, 'B': 30, 'C': 60, 'D': 85, 'E': 100}, 'Q10': {'A': 0, 'B': 30, 'C': 65, 'D': 100}}
WEIGHTS = {'capacity': 0.3, 'tolerance': 0.3, 'horizon': 0.2, 'liquidity': 0.1, 'knowledge': 0.1}
CATEGORIES = ['Very Conservative', 'Conservative', 'Moderate', 'Growth-Oriented', 'Aggressive']
RISK_POLICY = {
    "Conservative": {
        "policy_percentile": 35,
        "max_volatility": 0.25,
        "max_beta": 0.85,
        "max_single_weight": 0.45,
        "max_sector_weight": 0.60,
    },
    "Moderate": {
        "policy_percentile": 55,
        "max_volatility": 0.35,
        "max_beta": 1.05,
        "max_single_weight": 0.45,
        "max_sector_weight": 0.60,
    },
    "Growth-Oriented": {
        "policy_percentile": 75,
        "max_volatility": 0.45,
        "max_beta": 1.25,
        "max_single_weight": 0.50,
        "max_sector_weight": 0.65,
    },
    "Aggressive": {
        "policy_percentile": 95,
        "max_volatility": 0.55,
        "max_beta": 1.50,
        "max_single_weight": 0.55,
        "max_sector_weight": 0.70,
    },
}
RESTRICTED_POLICY_ADJUSTMENTS = {
    "policy_percentile_reduction": 15,
    "max_volatility_multiplier": 0.85,
    "max_beta_multiplier": 0.90,
    "max_single_weight_cap": 0.35,
    "max_sector_weight_cap": 0.50,
}
MIN_WEIGHT = 0.10
MIN_STOCKS = 30


def single_stock_cap(selected_count, policy_max):
    """Concentration ceiling for 3–15 selected stocks, also bounded by suitability.

    These tiers are an AURA design choice, not a regulatory limit. A fixed 10%
    or 20% cap would make a three-stock portfolio mathematically impossible.
    """
    if not 3 <= selected_count <= 15:
        raise ValueError("Please select between 3 and 15 stocks.")
    if selected_count == 3:
        count_cap = 0.40
    elif selected_count == 4:
        count_cap = 0.30
    elif selected_count == 5:
        count_cap = 0.25
    elif selected_count <= 7:
        count_cap = 0.20
    elif selected_count <= 11:
        count_cap = 0.15
    else:
        count_cap = 0.10
    return min(float(policy_max), count_cap)

def validate_answers(answers):

    if not isinstance(answers, dict):
        raise TypeError("Answers must be a dictionary.")

    required = set(ITEM_SCORES)
    provided = set(answers)

    missing = sorted(required - provided)
    unexpected = sorted(provided - required)

    if missing:
        raise ValueError(f"Missing answers: {missing}")

    if unexpected:
        raise ValueError(f"Unexpected questions: {unexpected}")

    cleaned = {}

    for question, options in ITEM_SCORES.items():

        answer = answers[question]

        if not isinstance(answer, str):
            raise ValueError(
                f"{question}: answer must be a letter."
            )

        answer = answer.strip().upper()

        if answer not in options:
            raise ValueError(
                f"{question}: invalid answer '{answer}'. "
                f"Allowed: {list(options)}"
            )

        cleaned[question] = answer

    return cleaned


def score_client(answers):

    answers = validate_answers(answers)

    s = {
        q: ITEM_SCORES[q][answers[q]]
        for q in ITEM_SCORES
    }

    # Risk Capacity
    rc = (
        0.40 * s["Q1"]
        + 0.60 * s["Q2"]
    )

    # Risk Tolerance
    rt = (
        0.25 * s["Q5"]
        + 0.20 * s["Q6"]
        + 0.15 * s["Q7"]
        + 0.15 * s["Q9"]
        + 0.25 * s["Q10"]
    )

    # Other dimensions
    hz = s["Q3"]
    lq = s["Q4"]
    kn = s["Q8"]

    # Overall Client Risk Score
    total = (
        WEIGHTS["capacity"] * rc
        + WEIGHTS["tolerance"] * rt
        + WEIGHTS["horizon"] * hz
        + WEIGHTS["liquidity"] * lq
        + WEIGHTS["knowledge"] * kn
    )

    # Conservative classification rule
    final_category = min(
        [category(total), category(rc), category(rt)],
        key=CATEGORIES.index
    )

    return {
        "answers": answers,
        "item_scores": s,
        "risk_capacity_score": rc,
        "risk_tolerance_score": rt,
        "investment_horizon_score": hz,
        "liquidity_score": lq,
        "investment_knowledge_score": kn,
        "client_risk_score": total,
        "client_risk_category": final_category,
    }


def suitability(answers, item_scores, final_category):

    block = []
    restrict = []

    # NOT_ELIGIBLE gates
    if answers["Q3"] == "A":
        block.append("G1")

    if answers["Q4"] == "A":
        block.append("G2")

    if answers["Q1"] == "A":
        block.append("G3")

    if answers["Q2"] == "A":
        block.append("G4")

    if answers["Q8"] == "A":
        block.append("G5")

    if final_category == "Very Conservative":
        block.append("G6")

    # Restricted gates
    if answers["Q3"] == "B":
        restrict.append("G7")

    if answers["Q4"] == "B":
        restrict.append("G8")

    if answers["Q8"] == "B":
        restrict.append("G9")

    # Inconsistent answers
    q5 = item_scores["Q5"]
    q6 = item_scores["Q6"]
    q10 = item_scores["Q10"]

    if abs(q5 - q10) >= 50 or abs(q5 - q6) >= 50:
        restrict.append("G10")

    if block:
        status = "NOT_ELIGIBLE"

    elif restrict:
        status = "ELIGIBLE_RESTRICTED"

    else:
        status = "ELIGIBLE"

    return status, block + restrict


def build_risk_policy(client, status):

    if status == "NOT_ELIGIBLE":
        return None

    cat = client["client_risk_category"]
    policy = dict(RISK_POLICY[cat])
    policy["client_risk_category"] = cat
    policy["restricted"] = status == "ELIGIBLE_RESTRICTED"

    if policy["restricted"]:
        policy["policy_percentile"] = max(
            0,
            policy["policy_percentile"]
            - RESTRICTED_POLICY_ADJUSTMENTS["policy_percentile_reduction"],
        )
        policy["max_volatility"] *= RESTRICTED_POLICY_ADJUSTMENTS[
            "max_volatility_multiplier"
        ]
        policy["max_beta"] *= RESTRICTED_POLICY_ADJUSTMENTS[
            "max_beta_multiplier"
        ]
        policy["max_single_weight"] = min(
            policy["max_single_weight"],
            RESTRICTED_POLICY_ADJUSTMENTS["max_single_weight_cap"],
        )
        policy["max_sector_weight"] = min(
            policy["max_sector_weight"],
            RESTRICTED_POLICY_ADJUSTMENTS["max_sector_weight_cap"],
        )

    policy["min_weight"] = MIN_WEIGHT

    return policy


def calibrate_stock_risk(stocks, policy):

    if policy is None:
        return None

    df = stocks.sort_values("risk_score").copy()

    s_policy = float(
        np.percentile(
            df["risk_score"],
            policy["policy_percentile"]
        )
    )

    return {
        **policy,
        "s_policy": s_policy,
        "max_stock_risk_score": s_policy,
    }


def prepare_stock_data(
    stocks,
    drawdown_unit="fraction"
):

    if drawdown_unit not in ("fraction", "percent"):
        raise ValueError(
            "drawdown_unit must be 'fraction' or 'percent'."
        )

    df = pd.DataFrame(stocks).copy()

    # Prevent ambiguous duplicate names
    new_columns = [
        COLUMN_ALIASES.get(col, col)
        for col in df.columns
    ]

    if len(new_columns) != len(set(new_columns)):
        raise ValueError(
            "Duplicate columns after renaming. "
            "Check COLUMN_ALIASES."
        )

    df.columns = new_columns

    if "stock_code" in df.columns:
        df["stock_code"] = (
            df["stock_code"]
            .astype(str)
            .str.strip()
            .str.zfill(6)
        )

    for col in ["company_name", "sector", "tier"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    required = [
        "stock_code",
        "company_name",
        "sector",
        "risk_score",
        "return_score",
        "max_drawdown_3y",
        "percentile_3y",
        "percentile_5y",
        "volatility_3y_annualised",
        "adjusted_beta_5y",
        "adjusted_beta_3y",
    ]

    missing = [
        col for col in required
        if col not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Missing stock columns: {missing}"
        )

    numeric_columns = [
        "risk_score",
        "return_score",
        "max_drawdown_3y",
        "percentile_3y",
        "percentile_5y",
        "volatility_3y_annualised",
        "adjusted_beta_5y",
        "adjusted_beta_3y",
    ]

    if "return_3y_pa" in df.columns:
        numeric_columns.append("return_3y_pa")

    for col in numeric_columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.replace([np.inf, -np.inf], np.nan)

    # Remove incomplete records
    df = df.dropna(subset=required).copy()

    # Risk/return/percentile scores must be 0–100
    score_columns = [
        "risk_score",
        "return_score",
        "percentile_3y",
        "percentile_5y",
    ]

    valid_scores = df[score_columns].ge(0).all(axis=1)
    valid_scores &= df[score_columns].le(100).all(axis=1)

    df = df.loc[valid_scores].copy()

    # Normalise drawdown to positive fractions
    df["max_drawdown_3y"] = df["max_drawdown_3y"].abs()

    if drawdown_unit == "percent":
        df["max_drawdown_3y"] /= 100.0

    if (df["max_drawdown_3y"] > 1).any():
        raise ValueError(
            "Invalid drawdown values. "
            "Check whether the source uses fractions or percentages."
        )

    if df["stock_code"].duplicated().any():
        raise ValueError(
            "Duplicate stock_code values detected."
        )

    if df.empty:
        raise ValueError(
            "No valid stock records remain after cleaning."
        )

    return df.reset_index(drop=True)


def run_aura(answers, stocks):

    # Step 1: Client risk scoring
    client = score_client(answers)

    # Step 2: Suitability assessment
    status, flags = suitability(
        client["answers"],
        client["item_scores"],
        client["client_risk_category"]
    )

    result = {
        "client_profile": client,
        "suitability_status": status,
        "gate_flags": flags,
        "calibration": None,
        "matching_stocks": pd.DataFrame(),
    }

    # Stop if client is not eligible
    if status == "NOT_ELIGIBLE":
        return result

    # Step 3: Risk policy, without any total-funds equity allocation.
    policy = build_risk_policy(client, status)

    # Step 4: Stock risk calibration
    calibration = calibrate_stock_risk(
        stocks,
        policy
    )

    result["calibration"] = calibration

    if calibration["max_stock_risk_score"] is None:

        result["suitability_status"] = "NOT_ELIGIBLE"

        result["gate_flags"].append(
            "NO_STOCK_FITS_RISK_POLICY"
        )

        return result

    # Step 5: Stock screening
    matching = screen_stocks(
        stocks,
        calibration["max_stock_risk_score"]
    )

    result["matching_stocks"] = matching

    return result


def construct_recommendations(
    matching_stocks,
    target_n=15,
    max_sector_weight=0.25,
    max_tier_weight=0.50
):
    df = matching_stocks.copy()

    required = [
        "stock_code",
        "company_name",
        "sector",
        "tier",
        "risk_score",
        "return_score",
        "selection_score"
    ]

    missing = [c for c in required if c not in df.columns]

    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df = df.dropna(subset=required)
    df = df.drop_duplicates(subset=["stock_code"]).copy()

    df["selection_score"] = pd.to_numeric(
        df["selection_score"],
        errors="coerce"
    )

    df = df.dropna(subset=["selection_score"])

    if len(df) < target_n:
        raise ValueError("Not enough valid stocks.")

    max_sector_count = int(
        np.floor(max_sector_weight * target_n + 1e-9)
    )

    max_tier_count = int(
        np.floor(max_tier_weight * target_n + 1e-9)
    )

    n = len(df)

    scores = df["selection_score"].to_numpy(dtype=float)

    constraints = []

    # Select exactly 15 recommendation candidates.
    constraints.append(
        LinearConstraint(
            np.ones((1, n)),
            lb=[target_n],
            ub=[target_n]
        )
    )

    # Sector diversification
    for sector in df["sector"].unique():

        mask = (
            df["sector"].to_numpy() == sector
        ).astype(float)

        constraints.append(
            LinearConstraint(
                mask.reshape(1, -1),
                lb=[0],
                ub=[max_sector_count]
            )
        )

    # Tier diversification
    for tier in df["tier"].unique():

        mask = (
            df["tier"].to_numpy() == tier
        ).astype(float)

        constraints.append(
            LinearConstraint(
                mask.reshape(1, -1),
                lb=[0],
                ub=[max_tier_count]
            )
        )

    solution = milp(
        c=-scores,
        integrality=np.ones(n),
        bounds=Bounds(np.zeros(n), np.ones(n)),
        constraints=constraints,
        options={"time_limit": 30}
    )

    if solution.status != 0 or solution.x is None:
        raise ValueError(
            "No verified optimal portfolio found. "
            f"Solver message: {solution.message}"
        )

    selected = df.iloc[
        np.where(solution.x > 0.5)[0]
    ].copy()

    selected = selected.sort_values(
        "selection_score",
        ascending=False
    ).reset_index(drop=True)

    selected["recommendation_rank"] = np.arange(1, len(selected) + 1)

    return selected


def construct_portfolio(*args, **kwargs):
    raise ValueError(
        "The old automatic 15-stock portfolio has been retired. "
        "Use construct_recommendations for the 15-stock candidate list, "
        "then optimise weights only after the client selects 3-15 stocks."
    )


def load_covariance_matrix(path, sheet_name="Covariance"):
    matrix = pd.read_excel(path, sheet_name=sheet_name)

    if "ticker" not in matrix.columns:
        raise ValueError("Covariance matrix must include a ticker column.")

    matrix["ticker"] = (
        matrix["ticker"].astype(str).str.strip().str.zfill(6)
    )
    matrix = matrix.set_index("ticker")
    matrix.columns = [
        str(col).strip().zfill(6)
        for col in matrix.columns
    ]
    matrix = matrix.apply(pd.to_numeric, errors="coerce")

    return matrix


def optimise_selected_portfolio(selected_stocks, covariance_matrix, policy):
    df = selected_stocks.copy()

    if not 3 <= len(df) <= 15:
        raise ValueError("Please select between 3 and 15 stocks.")

    required = [
        "stock_code",
        "company_name",
        "sector",
        "risk_score",
        "return_score",
        "adjusted_beta_5y",
        "adjusted_beta_3y",
    ]
    missing = [col for col in required if col not in df.columns]

    if missing:
        raise ValueError(f"Missing selected-stock columns: {missing}")

    if df["stock_code"].duplicated().any():
        raise ValueError("Selected stocks must be unique.")

    codes = df["stock_code"].astype(str).str.zfill(6).tolist()
    missing_covariance = [
        code for code in codes
        if code not in covariance_matrix.index
        or code not in covariance_matrix.columns
    ]

    if missing_covariance:
        raise ValueError(
            "Cannot optimise without a same-period covariance matrix for: "
            + ", ".join(missing_covariance)
        )

    covariance = (
        covariance_matrix.loc[codes, codes]
        .to_numpy(dtype=float)
    )

    if not np.isfinite(covariance).all():
        raise ValueError("Covariance matrix contains missing or invalid values.")

    covariance = (covariance + covariance.T) / 2

    beta_5y = df["adjusted_beta_5y"].to_numpy(dtype=float)
    beta_3y = df["adjusted_beta_3y"].to_numpy(dtype=float)
    return_score = df["return_score"].to_numpy(dtype=float) / 100.0

    policy = dict(policy)
    policy["min_weight"] = min(policy["min_weight"], 0.5 / len(df))
    policy["max_single_weight"] = single_stock_cap(
        len(df), policy["max_single_weight"]
    )
    min_weight = policy["min_weight"]
    max_single = policy["max_single_weight"]

    if len(df) * min_weight > 1 + 1e-9:
        raise ValueError("Minimum weight rule is infeasible for this selection.")

    if max_single * len(df) < 1 - 1e-9:
        raise ValueError(
            f"The {max_single:.0%} single-stock cap cannot accommodate "
            f"{len(df)} selected stock(s). Select more stocks; the cap remains unchanged."
        )

    def portfolio_variance(weights):
        return float(weights @ covariance @ weights)

    def portfolio_volatility(weights):
        return float(np.sqrt(max(portfolio_variance(weights), 0)))

    constraints = [
        {"type": "eq", "fun": lambda weights: np.sum(weights) - 1.0},
        {
            "type": "ineq",
            "fun": lambda weights: policy["max_volatility"]
            - portfolio_volatility(weights),
        },
        {
            "type": "ineq",
            "fun": lambda weights: policy["max_beta"]
            - float(weights @ beta_5y),
        },
        {
            "type": "ineq",
            "fun": lambda weights: policy["max_beta"]
            - float(weights @ beta_3y),
        },
    ]

    sectors = df["sector"].fillna("Unknown").astype(str).to_numpy()

    for sector in sorted(set(sectors)):
        mask = (sectors == sector).astype(float)
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda weights, mask=mask: policy["max_sector_weight"]
                - float(weights @ mask),
            }
        )

    def objective(weights):
        # Primary goal: minimise covariance-based annual volatility.
        # Tiny tie-breaker: prefer stronger return scores without overpowering risk.
        return portfolio_variance(weights) - 0.0001 * float(weights @ return_score)

    initial = np.repeat(1 / len(df), len(df))
    bounds = Bounds(
        np.repeat(min_weight, len(df)),
        np.repeat(max_single, len(df)),
    )

    solution = minimize(
        objective,
        initial,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )

    if not solution.success or solution.x is None:
        raise ValueError(
            "No feasible risk-based optimisation result was found. "
            f"Solver message: {solution.message}"
        )

    weights = np.clip(solution.x, 0, 1)
    weights = weights / weights.sum()
    volatility = portfolio_volatility(weights)
    beta_5y_value = float(weights @ beta_5y)
    beta_3y_value = float(weights @ beta_3y)
    sector_weights = {
        sector: float(weights[sectors == sector].sum())
        for sector in sorted(set(sectors))
    }

    tolerance = 1e-6
    violations = []

    if volatility > policy["max_volatility"] + tolerance:
        violations.append("annualised volatility")

    if beta_5y_value > policy["max_beta"] + tolerance:
        violations.append("5Y adjusted beta")

    if beta_3y_value > policy["max_beta"] + tolerance:
        violations.append("3Y adjusted beta")

    if max(weights) > policy["max_single_weight"] + tolerance:
        violations.append("single-stock concentration")

    if max(sector_weights.values()) > policy["max_sector_weight"] + tolerance:
        violations.append("sector concentration")

    if violations:
        raise ValueError(
            "Optimisation result failed risk checks: "
            + ", ".join(violations)
        )

    result = df.copy()
    result["weight"] = weights
    result = result.sort_values("weight", ascending=False).reset_index(drop=True)

    return {
        "portfolio": result,
        "summary": {
            "portfolio_volatility": volatility,
            "portfolio_beta_5y": beta_5y_value,
            "portfolio_beta_3y": beta_3y_value,
            "weighted_average_risk_score": float(
                (result["risk_score"] * result["weight"]).sum()
            ),
            "weighted_average_return_score": float(
                (result["return_score"] * result["weight"]).sum()
            ),
            "sector_weights": sector_weights,
        },
        "constraints": dict(policy),
    }


def preview_amount_adjustment(current_result, covariance_matrix, total_amount,
                              stock_code, new_amount):
    portfolio = current_result["portfolio"].copy().reset_index(drop=True)
    policy = current_result["constraints"]
    if not np.isfinite([total_amount, new_amount]).all():
        raise ValueError("Investment amounts must be finite.")
    total_cents = int(round(total_amount * 100))
    target_cents = int(round(new_amount * 100))
    if total_cents <= 0 or not 0 < target_cents < total_cents:
        raise ValueError("Enter an amount greater than zero and less than the total investment.")
    target = portfolio["stock_code"].eq(stock_code)
    if target.sum() != 1 or len(portfolio) < 2:
        raise ValueError("Select one stock from a portfolio containing at least two stocks.")
    other_weights = portfolio.loc[~target, "weight"]
    if not np.isfinite(other_weights).all() or other_weights.sum() <= 0:
        raise ValueError("The remaining portfolio weights are invalid.")
    exact = other_weights / other_weights.sum() * (total_cents - target_cents)
    cents = exact.astype(int)
    remainder = total_cents - target_cents - int(cents.sum())
    cents.loc[(exact - cents).sort_values(ascending=False, kind="stable").index[:remainder]] += 1
    portfolio.loc[target, "investment_amount"] = target_cents / 100
    portfolio.loc[~target, "investment_amount"] = cents / 100
    portfolio["weight"] = portfolio["investment_amount"] / (total_cents / 100)
    codes = portfolio["stock_code"].astype(str).str.zfill(6).tolist()
    missing = [code for code in codes if code not in covariance_matrix.index
               or code not in covariance_matrix.columns]
    if missing:
        raise ValueError("Missing same-period covariance data for: " + ", ".join(missing))
    covariance = covariance_matrix.loc[codes, codes].to_numpy(dtype=float)
    if not np.isfinite(covariance).all():
        raise ValueError("Covariance matrix contains missing or invalid values.")
    weights = portfolio["weight"].to_numpy()
    beta5 = portfolio["adjusted_beta_5y"].to_numpy(dtype=float)
    beta3 = portfolio["adjusted_beta_3y"].to_numpy(dtype=float)
    if not np.isfinite(beta5).all() or not np.isfinite(beta3).all():
        raise ValueError("Adjusted beta data contains missing or invalid values.")
    volatility = float(np.sqrt(max(float(weights @ covariance @ weights), 0)))
    sector_weights = portfolio.groupby("sector", dropna=False)["weight"].sum()
    checks = [
        ("Annualised volatility", volatility, policy["max_volatility"], "maximum"),
        ("5Y adjusted beta", float(weights @ beta5), policy["max_beta"], "maximum"),
        ("3Y adjusted beta", float(weights @ beta3), policy["max_beta"], "maximum"),
    ]
    for row in portfolio.itertuples():
        checks.extend([
            (f"{row.stock_code}: minimum weight", row.weight, policy["min_weight"], "minimum"),
            (f"{row.stock_code}: single-stock weight", row.weight, policy["max_single_weight"], "maximum"),
        ])
    checks.extend((f"{sector}: sector weight", float(weight), policy["max_sector_weight"], "maximum")
                  for sector, weight in sector_weights.items())
    check_frame = pd.DataFrame(checks, columns=["check", "value", "limit", "rule"])
    check_frame["passed"] = [
        value >= limit - 1e-8 if rule == "minimum" else value <= limit + 1e-8
        for _, value, limit, rule in checks
    ]
    return {
        "portfolio": portfolio,
        "constraints": dict(policy),
        "mode": "manual",
        "checks": check_frame,
        "valid": bool(check_frame["passed"].all()),
        "summary": {
            "portfolio_volatility": volatility,
            "portfolio_beta_5y": float(weights @ beta5),
            "portfolio_beta_3y": float(weights @ beta3),
            "weighted_average_risk_score": float((portfolio["risk_score"] * portfolio["weight"]).sum()),
            "weighted_average_return_score": float((portfolio["return_score"] * portfolio["weight"]).sum()),
            "sector_weights": sector_weights.to_dict(),
        },
    }


def allocate_investment_amounts(portfolio, total_amount):
    """Allocate an investment amount to optimised weights in exact cents.

    This amount belongs to the selected-stock portfolio. It never implies an
    allocation of the client's total wealth to equities.
    """
    result = portfolio.copy().reset_index(drop=True)
    if not 3 <= len(result) <= 15 or "weight" not in result:
        raise ValueError("A portfolio of 3 to 15 weighted stocks is required.")
    if not np.isfinite(total_amount) or not 0 < total_amount <= 1e12:
        raise ValueError("Enter a finite investment amount greater than zero.")
    total_cents = int(round(float(total_amount) * 100))
    if total_cents <= 0:
        raise ValueError("The investment amount must be at least one cent.")
    weights = result["weight"].to_numpy(dtype=float)
    if not np.isfinite(weights).all() or (weights <= 0).any() or abs(weights.sum() - 1) > 1e-6:
        raise ValueError("Portfolio weights must be positive and sum to 100%.")
    exact = weights / weights.sum() * total_cents
    cents = np.floor(exact).astype(np.int64)
    remainder = total_cents - int(cents.sum())
    order = np.argsort(-(exact - cents), kind="stable")
    cents[order[:remainder]] += 1
    if (cents <= 0).any():
        raise ValueError("The investment amount is too small for the selected stocks.")
    result["investment_amount"] = cents / 100
    return result


def category(score):

    if not np.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("Risk score must be between 0 and 100.")

    index = sum(score >= cutoff for cutoff in CATEGORY_CUTS)

    return CATEGORIES[index]


def screen_stocks(stocks, max_risk_score):

    if max_risk_score is None:
        return pd.DataFrame()

    eligible = stocks[
        (stocks["risk_score"] <= max_risk_score)
        & (stocks["percentile_3y"] >= 40)
        & (stocks["percentile_5y"] >= 40)
    ].copy()

    eligible["selection_score"] = (
        eligible["return_score"]
        - 0.5 * np.maximum(
            0,
            eligible["risk_score"] - (max_risk_score - 15)
        )
    )

    eligible = eligible.sort_values(
        "selection_score",
        ascending=False
    )

    return eligible
