"""AURA calculation and persistence service called inside Streamlit Cloud."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import UUID

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from postgrest import SyncPostgrestClient

from .aura_engine import (allocate_investment_amounts, construct_recommendations,
                          load_covariance_matrix, optimise_selected_portfolio,
                          prepare_stock_data, preview_amount_adjustment,
                          run_aura, score_client, suitability)

log = logging.getLogger(__name__)
COVARIANCE_PATH = Path(__file__).resolve().parent.parent / 'AURA_Covariance.xlsx'
METHODOLOGY = 'aura-python-v1'
BUCKET = 'aura-reports'
SUITABILITY = {'ELIGIBLE': 'suitable', 'ELIGIBLE_RESTRICTED': 'review_required',
               'NOT_ELIGIBLE': 'unsuitable'}
app = FastAPI(title='AURA Wealth Advisory API', version='2.1.0')


def config():
    url = os.getenv('SUPABASE_URL', '').rstrip('/')
    secret = (os.getenv('SUPABASE_SECRET_KEY', '').strip()
              or os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip())
    if not url or not secret:
        raise RuntimeError('SUPABASE_URL and SUPABASE_SECRET_KEY are required')
    return url, secret


def admin_headers(secret: str) -> dict[str, str]:
    """New Supabase secret keys go in apikey, never in Bearer Authorization."""
    headers = {'apikey': secret}
    if not secret.startswith('sb_secret_'):
        headers['Authorization'] = 'Bearer ' + secret
    return headers


class AdminDatabase:
    def __init__(self, url: str, secret: str):
        self.client = SyncPostgrestClient(url + '/rest/v1',
                                          headers=admin_headers(secret))

    def table(self, name: str):
        return self.client.from_(name)


def admin():
    url, secret = config()
    return AdminDatabase(url, secret)


def current_user(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Sign in required')
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, 'Sign in required')
    try:
        url, secret = config()
        req = Request(url + '/auth/v1/user', headers={
            'apikey': secret, 'Authorization': 'Bearer ' + token})
        with urlopen(req, timeout=10) as response:
            return str(UUID(json.load(response)['id']))
    except (HTTPError, URLError, KeyError, ValueError):
        raise HTTPException(401, 'Invalid or expired session') from None


def find_one(table: str, user_id: str, **filters):
    q = admin().table(table).select('*').eq('user_id', user_id)
    for key, value in filters.items():
        q = q.eq(key, value)
    data = q.limit(1).execute().data or []
    return data[0] if data else None


def owned(table: str, user_id: str, **filters):
    row = find_one(table, user_id, **filters)
    if row is None:
        raise HTTPException(404, 'Record not found')
    return row


def event(user_id: str, analysis_id: str, name: str, data=None):
    try:
        admin().table('aura_activity_logs').insert({
            'analysis_id': analysis_id, 'user_id': user_id,
            'event_type': name, 'event_data': data or {},
        }).execute()
    except Exception:
        # A saved result must still be returned if an optional audit event fails.
        log.exception('Could not write activity event %s for analysis %s', name, analysis_id)


def serialise(df):
    return json.loads(df.to_json(orient='records', force_ascii=False))


def fetch_stocks():
    db, rows, offset = admin(), [], 0
    while True:
        batch = (db.table('stock_metrics_excel').select('*').order('stock_code')
                 .range(offset, offset + 499).execute().data or [])
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    for row in rows:
        row['stock_code'] = str(row['stock_code']).strip().zfill(6)
    return rows, prepare_stock_data(rows, drawdown_unit='fraction')


def answers_from(row):
    return {entry['question']: entry['answer'] for entry in row['questionnaire_answers']}


class ScoreRequest(BaseModel):
    answers: dict[str, str]


class AnalysisRequest(BaseModel):
    analysis_id: UUID


class OptimiseRequest(AnalysisRequest):
    selected_codes: list[str] = Field(min_length=3, max_length=15)
    total_amount: float | None = Field(default=None, gt=0)
    currency: Literal['EUR', 'CNY', 'USD', 'GBP'] = 'EUR'


class AdjustAmountRequest(AnalysisRequest):
    stock_code: str = Field(min_length=1)
    new_amount: float = Field(gt=0)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'covariance_loaded': COVARIANCE_PATH.exists()}


@app.get('/api/stocks')
def stocks(user_id: str = Depends(current_user)):
    try:
        raw, clean = fetch_stocks()
        return {'raw_count': len(raw), 'clean_count': len(clean), 'stocks': serialise(clean)}
    except Exception:
        log.exception('Stock retrieval failed')
        raise HTTPException(503, 'Stock data unavailable') from None


@app.post('/api/score')
def score(req: ScoreRequest, user_id: str = Depends(current_user)):
    try:
        profile = score_client(req.answers)
        status, flags = suitability(profile['answers'], profile['item_scores'],
                                    profile['client_risk_category'])
        db = admin()
        db.table('aura_users').upsert({'user_id': user_id}, on_conflict='user_id').execute()
        answers = [{'question': f'Q{i}', 'answer': profile['answers'][f'Q{i}']}
                   for i in range(1, 11)]
        row = db.table('aura_assessments').insert({
            'user_id': user_id, 'questionnaire_answers': answers,
            'risk_score': profile['client_risk_score'],
            'risk_category': profile['client_risk_category'],
            'suitability_status': SUITABILITY[status],
            'suitability_reason': ', '.join(flags) if flags else None,
            'methodology_version': METHODOLOGY,
        }).execute().data[0]
        event(user_id, row['analysis_id'], 'assessment_completed', {'suitability': status})
        return {'analysis_id': row['analysis_id'], 'client_profile': profile,
                'suitability_status': status, 'gate_flags': flags}
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        log.exception('Assessment write failed')
        raise HTTPException(503, 'Could not save assessment') from None


@app.post('/api/screen')
def screen(req: AnalysisRequest, user_id: str = Depends(current_user)):
    analysis_id = str(req.analysis_id)
    assessment = owned('aura_assessments', user_id, analysis_id=analysis_id)
    existing = find_one('aura_recommendations', user_id, analysis_id=analysis_id)
    profile = score_client(answers_from(assessment))
    status, flags = suitability(profile['answers'], profile['item_scores'],
                                profile['client_risk_category'])
    if existing:
        criteria = existing['screening_criteria']
        return {'analysis_id': analysis_id, 'client_profile': profile,
                'suitability_status': status, 'gate_flags': flags,
                'raw_stock_count': criteria.get('raw_stock_count', 0),
                'clean_stock_count': criteria.get('clean_stock_count', 0),
                'calibration': criteria['calibration'], 'matching_stocks': [],
                'recommendations': existing['stocks_snapshot']}
    try:
        raw, clean = fetch_stocks()
        result = run_aura(answers_from(assessment), clean)
        response = {'analysis_id': analysis_id, 'raw_stock_count': len(raw),
                    'clean_stock_count': len(clean), 'client_profile': result['client_profile'],
                    'suitability_status': result['suitability_status'],
                    'gate_flags': result['gate_flags'], 'calibration': result.get('calibration'),
                    'matching_stocks': [], 'recommendations': []}
        if result['suitability_status'] == 'NOT_ELIGIBLE':
            return response
        matching = result['matching_stocks']
        response['matching_stocks'] = serialise(matching)
        if matching.empty:
            return response
        picks = construct_recommendations(matching, target_n=15,
                                          max_sector_weight=0.25, max_tier_weight=0.50)
        if len(picks) != 15:
            raise HTTPException(422, 'A complete 15-stock list is unavailable')
        snapshot = serialise(picks)
        admin().table('aura_recommendations').insert({
            'analysis_id': analysis_id, 'user_id': user_id,
            'stocks_snapshot': snapshot,
            'screening_criteria': {'calibration': result['calibration'],
                                   'raw_stock_count': len(raw), 'clean_stock_count': len(clean)},
            'methodology_version': METHODOLOGY,
        }).execute()
        event(user_id, analysis_id, 'recommendations_created', {'count': 15})
        response['recommendations'] = snapshot
        return response
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        log.exception('Screening write failed')
        raise HTTPException(503, 'Could not save recommendations') from None


@app.post('/api/optimise')
def optimise(req: OptimiseRequest, user_id: str = Depends(current_user)):
    analysis_id = str(req.analysis_id)
    owned('aura_assessments', user_id, analysis_id=analysis_id)
    rec = owned('aura_recommendations', user_id, analysis_id=analysis_id)
    codes = [c.strip().zfill(6) for c in req.selected_codes]
    allowed = {r['stock_code'] for r in rec['stocks_snapshot']}
    if len(set(codes)) != len(codes) or not set(codes).issubset(allowed):
        raise HTTPException(400, 'Select 3 to 15 distinct stocks from this analysis')
    if find_one('aura_reports', user_id, analysis_id=analysis_id):
        raise HTTPException(409, 'Report already queued; start a new assessment')
    try:
        selected = pd.DataFrame([r for r in rec['stocks_snapshot'] if r['stock_code'] in codes])
        covariance = load_covariance_matrix(str(COVARIANCE_PATH))
        result = optimise_selected_portfolio(selected, covariance,
            rec['screening_criteria']['calibration'])
        portfolio = result['portfolio']
        if req.total_amount is not None:
            portfolio = allocate_investment_amounts(portfolio, req.total_amount)
        positions = serialise(portfolio)
        if not 3 <= len(positions) <= 15 or abs(sum(p['weight'] for p in positions) - 1) > 1e-6:
            raise ValueError('Portfolio weights did not sum to 100%')
        total_amount = (round(sum(p['investment_amount'] for p in positions), 2)
                        if req.total_amount is not None else None)
        inputs = {'selected_codes': codes, 'constraints': result['constraints'],
                  'mode': 'optimised', 'total_amount': total_amount,
                  'currency': req.currency,
                  'baseline_positions': positions,
                  'baseline_risk_metrics': result['summary']}
        admin().table('aura_portfolios').upsert({
            'analysis_id': analysis_id, 'user_id': user_id,
            'positions': positions, 'risk_metrics': result['summary'],
            'optimization_inputs': inputs,
            'methodology_version': METHODOLOGY,
        }, on_conflict='analysis_id').execute()
        event(user_id, analysis_id, 'portfolio_optimised', {'selected_codes': codes})
        return {'analysis_id': analysis_id, 'portfolio': positions,
                'summary': result['summary'], 'constraints': result['constraints'],
                'total_amount': total_amount, 'currency': req.currency}
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        log.exception('Optimisation write failed')
        raise HTTPException(503, 'Could not save portfolio') from None


@app.post('/api/portfolio/adjust')
def adjust_portfolio_amount(req: AdjustAmountRequest, user_id: str = Depends(current_user)):
    analysis_id = str(req.analysis_id)
    record = owned('aura_portfolios', user_id, analysis_id=analysis_id)
    if find_one('aura_reports', user_id, analysis_id=analysis_id):
        raise HTTPException(409, 'Report already queued; start a new assessment')
    inputs = dict(record['optimization_inputs'])
    total_amount = inputs.get('total_amount')
    if not total_amount:
        raise HTTPException(409, 'Enter an investment amount before adjusting stocks')
    baseline = inputs.get('baseline_positions') or record['positions']
    try:
        current = {'portfolio': pd.DataFrame(record['positions']),
                   'constraints': inputs['constraints']}
        covariance = load_covariance_matrix(str(COVARIANCE_PATH))
        preview = preview_amount_adjustment(
            current, covariance, float(total_amount),
            req.stock_code.strip().zfill(6), req.new_amount)
        adjusted = serialise(preview['portfolio'])
        checks = serialise(preview['checks'])
        if preview['valid']:
            inputs.update({'mode': 'manual', 'last_adjustment': {
                'stock_code': req.stock_code.strip().zfill(6),
                'new_amount': round(req.new_amount, 2)}})
            updated = (admin().table('aura_portfolios').update({
                'positions': adjusted, 'risk_metrics': preview['summary'],
                'optimization_inputs': inputs,
            }).eq('analysis_id', analysis_id).eq('user_id', user_id).execute().data or [])
            if not updated:
                raise HTTPException(409, 'Portfolio changed; reload and try again')
            event(user_id, analysis_id, 'portfolio_amount_adjusted',
                  {'stock_code': req.stock_code.strip().zfill(6),
                   'new_amount': round(req.new_amount, 2)})
        return {'analysis_id': analysis_id, 'total_amount': total_amount,
                'original_portfolio': baseline,
                'original_summary': inputs.get('baseline_risk_metrics'),
                'adjusted_portfolio': adjusted,
                'adjusted_summary': preview['summary'],
                'checks': checks, 'valid': preview['valid'],
                'saved': preview['valid']}
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        log.exception('Amount adjustment failed')
        raise HTTPException(503, 'Could not adjust portfolio') from None


@app.post('/api/portfolio/reset')
def reset_portfolio_amounts(req: AnalysisRequest, user_id: str = Depends(current_user)):
    analysis_id = str(req.analysis_id)
    record = owned('aura_portfolios', user_id, analysis_id=analysis_id)
    if find_one('aura_reports', user_id, analysis_id=analysis_id):
        raise HTTPException(409, 'Report already queued; start a new assessment')
    inputs = dict(record['optimization_inputs'])
    baseline = inputs.get('baseline_positions')
    baseline_metrics = inputs.get('baseline_risk_metrics')
    if not baseline or not baseline_metrics:
        raise HTTPException(409, 'Original optimised portfolio is unavailable')
    inputs['mode'] = 'optimised'
    inputs.pop('last_adjustment', None)
    try:
        updated = (admin().table('aura_portfolios').update({
            'positions': baseline, 'risk_metrics': baseline_metrics,
            'optimization_inputs': inputs,
        }).eq('analysis_id', analysis_id).eq('user_id', user_id).execute().data or [])
        if not updated:
            raise HTTPException(409, 'Portfolio changed; reload and try again')
        event(user_id, analysis_id, 'portfolio_amounts_restored')
        return {'analysis_id': analysis_id, 'portfolio': baseline,
                'summary': baseline_metrics, 'constraints': inputs['constraints'],
                'total_amount': inputs.get('total_amount')}
    except HTTPException:
        raise
    except Exception:
        log.exception('Portfolio reset failed')
        raise HTTPException(503, 'Could not restore portfolio') from None


@app.get('/api/history')
def history(user_id: str = Depends(current_user)):
    db = admin()
    assessments = (db.table('aura_assessments').select('*').eq('user_id', user_id)
                   .order('created_at', desc=True).execute().data or [])
    recs = (db.table('aura_recommendations').select('*').eq('user_id', user_id)
            .execute().data or [])
    ports = (db.table('aura_portfolios').select('*').eq('user_id', user_id)
             .execute().data or [])
    by_rec = {r['analysis_id']: r for r in recs}
    by_port = {p['analysis_id']: p for p in ports}
    return [{'assessment': a, 'recommendations': by_rec.get(a['analysis_id']),
             'portfolio': by_port.get(a['analysis_id'])} for a in assessments]


@app.post('/api/reports')
def enqueue_report(req: AnalysisRequest, user_id: str = Depends(current_user)):
    analysis_id = str(req.analysis_id)
    owned('aura_portfolios', user_id, analysis_id=analysis_id)
    existing = find_one('aura_reports', user_id, analysis_id=analysis_id)
    if existing:
        return existing
    row = admin().table('aura_reports').insert({
        'analysis_id': analysis_id, 'user_id': user_id, 'status': 'pending',
        'storage_bucket': BUCKET,
    }).execute().data[0]
    event(user_id, analysis_id, 'report_queued', {'report_id': row['report_id']})
    return row


@app.post('/api/reports/{report_id}/retry')
def retry_report(report_id: UUID, user_id: str = Depends(current_user)):
    row = owned('aura_reports', user_id, report_id=str(report_id))
    if row['status'] != 'failed':
        raise HTTPException(409, 'Only a failed report can be retried')
    updated = (admin().table('aura_reports').update({
        'status': 'pending', 'started_at': None, 'failed_at': None,
        'error_code': None, 'storage_path': None,
    }).eq('report_id', str(report_id)).eq('user_id', user_id)
      .eq('status', 'failed').execute().data or [])
    if not updated:
        raise HTTPException(409, 'Report status changed; refresh and try again')
    event(user_id, row['analysis_id'], 'report_requeued', {'report_id': str(report_id)})
    return updated[0]


@app.get('/api/reports')
def reports(user_id: str = Depends(current_user)):
    return (admin().table('aura_reports').select('*').eq('user_id', user_id)
            .order('created_at', desc=True).execute().data or [])


@app.get('/api/reports/{report_id}/download')
def download_report(report_id: UUID, user_id: str = Depends(current_user)):
    row = find_one('aura_reports', user_id, report_id=str(report_id))
    if not row or row['status'] != 'completed' or not row['storage_path']:
        raise HTTPException(404, 'Completed report not found')
    if row['storage_bucket'] != BUCKET or not row['storage_path'].startswith(
            f"{user_id}/{row['analysis_id']}/"):
        raise HTTPException(404, 'Completed report not found')
    url, secret = config()
    path = quote(row['storage_path'], safe='/')
    req = Request(f'{url}/storage/v1/object/sign/{BUCKET}/{path}',
        data=b'{"expiresIn":60}', method='POST', headers={
            **admin_headers(secret),
            'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=10) as response:
            signed = json.load(response)['signedURL']
    except (HTTPError, URLError, KeyError):
        raise HTTPException(503, 'Report download unavailable') from None
    public_url = signed if signed.startswith('https://') else (
        url + signed if signed.startswith('/storage/v1/') else url + '/storage/v1' + signed)
    return {'url': public_url,
            'expires_in': 60}
