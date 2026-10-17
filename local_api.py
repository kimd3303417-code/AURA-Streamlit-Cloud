"""Call AURA's Python service directly inside Streamlit, without a web backend."""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from backend import server


def call(path: str, method: str, payload: dict | None, access_token: str):
    """Verify the Supabase session and dispatch only known AURA operations."""
    try:
        user_id = server.current_user('Bearer ' + access_token)
        data = payload or {}
        route = (method.upper(), path)
        if route == ('POST', '/api/score'):
            return server.score(server.ScoreRequest(**data), user_id)
        if route == ('POST', '/api/screen'):
            return server.screen(server.AnalysisRequest(**data), user_id)
        if route == ('POST', '/api/optimise'):
            return server.optimise(server.OptimiseRequest(**data), user_id)
        if route == ('POST', '/api/portfolio/adjust'):
            return server.adjust_portfolio_amount(server.AdjustAmountRequest(**data), user_id)
        if route == ('POST', '/api/portfolio/reset'):
            return server.reset_portfolio_amounts(server.AnalysisRequest(**data), user_id)
        if route == ('GET', '/api/history'):
            return server.history(user_id)
        if route == ('POST', '/api/reports'):
            return server.enqueue_report(server.AnalysisRequest(**data), user_id)
        if route == ('GET', '/api/reports'):
            return server.reports(user_id)
        report_route = re.fullmatch(r'/api/reports/([0-9a-fA-F-]{36})/(retry|download)', path)
        if report_route:
            report_id = UUID(report_route.group(1))
            action = report_route.group(2)
            if method.upper() == 'POST' and action == 'retry':
                return server.retry_report(report_id, user_id)
            if method.upper() == 'GET' and action == 'download':
                return server.download_report(report_id, user_id)
        raise RuntimeError('Unknown AURA operation')
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from None
    except (ValidationError, ValueError):
        raise RuntimeError('The request is invalid. Refresh and try again.') from None
