import { createClient } from '@supabase/supabase-js'
import { PDFDocument, StandardFonts, rgb } from 'pdf-lib'

const BUCKET = 'aura-reports'

function adminClient() {
  const url = Deno.env.get('SUPABASE_URL')
  const keys = JSON.parse(Deno.env.get('SUPABASE_SECRET_KEYS') || '{}')
  const key = keys.default || Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')
  if (!url || !key) throw new Error('Supabase admin configuration is missing')
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } })
}

async function audit(db, row, eventType) {
  const { error } = await db.from('aura_activity_logs').insert({
    analysis_id: row.analysis_id, user_id: row.user_id,
    event_type: eventType, event_data: { report_id: row.report_id },
  })
  if (error) console.error('Could not save report activity event', eventType)
}

async function recoverInterrupted(db) {
  const cutoff = new Date(Date.now() - 30 * 60 * 1000).toISOString()
  const { data: rows, error } = await db.from('aura_reports')
    .select('report_id,analysis_id,user_id,started_at')
    .eq('status', 'processing').lt('started_at', cutoff).limit(10)
  if (error) throw error
  for (const row of rows || []) {
    const { data, error: updateError } = await db.from('aura_reports')
      .update({ status: 'failed', failed_at: new Date().toISOString(),
                error_code: 'worker_interrupted' })
      .eq('report_id', row.report_id).eq('status', 'processing')
      .lt('started_at', cutoff).select('report_id')
    if (updateError) throw updateError
    if (data?.length) await audit(db, row, 'report_failed')
  }
}

async function claimNext(db) {
  const { data: waiting, error } = await db.from('aura_reports').select('*')
    .eq('status', 'pending').order('created_at', { ascending: true }).limit(5)
  if (error) throw error
  for (const row of waiting || []) {
    const { data: claimed, error: claimError } = await db.from('aura_reports')
      .update({ status: 'processing', started_at: new Date().toISOString(),
                error_code: null })
      .eq('report_id', row.report_id).eq('status', 'pending').select('*')
    if (claimError) throw claimError
    if (claimed?.length) return claimed[0]
  }
  return null
}

async function ownedSnapshot(db, table, row) {
  const { data, error } = await db.from(table).select('*')
    .eq('analysis_id', row.analysis_id).eq('user_id', row.user_id).single()
  if (error || !data) throw new Error(`Required ${table} snapshot is unavailable`)
  return data
}

async function askGemini(facts) {
  const apiKey = Deno.env.get('GEMINI_API_KEY')
  const model = (Deno.env.get('GEMINI_MODEL') || '').replace(/^models\//, '')
  if (!apiKey || !model) throw new Error('Gemini configuration is missing')
  const prompt = [
    'Write a concise English AURA wealth advisory explanation as plain text, at most 700 words.',
    'Use only ASCII characters and no tables. Explain the saved Python assessment and portfolio.',
    'Do not recalculate or change suitability, risk scores, stocks, weights, beta, or volatility.',
    'Do not invent prices, forecasts, returns, or guarantees.',
    'The portfolio weights total 100% of the selected-stock amount, not the customer\'s total assets.',
    'State that this is a model-generated explanation for review.',
    JSON.stringify(facts),
  ].join('\n')
  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`,
    { method: 'POST', signal: AbortSignal.timeout(90000),
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
      body: JSON.stringify({ contents: [{ role: 'user', parts: [{ text: prompt }] }],
                             generationConfig: { temperature: 0.2, maxOutputTokens: 1800 } }) },
  )
  if (!response.ok) throw new Error(`Gemini request failed (${response.status})`)
  const result = await response.json()
  const text = (result.candidates?.[0]?.content?.parts || [])
    .map((part) => part.text || '').join('\n').trim()
  if (!text) throw new Error('Gemini returned an empty report')
  return text.slice(0, 12000)
}

function ascii(text) {
  return text.normalize('NFKD').replace(/[^\x20-\x7E]/g, '?')
}

export async function pdfFromText(text) {
  const pdf = await PDFDocument.create()
  const regular = await pdf.embedFont(StandardFonts.Helvetica)
  const bold = await pdf.embedFont(StandardFonts.HelveticaBold)
  const pageSize = [595.28, 841.89]
  const margin = 48
  let page = pdf.addPage(pageSize)
  let y = pageSize[1] - margin
  page.drawText('AURA WEALTH ADVISORY REPORT', {
    x: margin, y, size: 16, font: bold, color: rgb(0.07, 0.14, 0.23),
  })
  y -= 32
  const width = pageSize[0] - margin * 2
  for (const raw of text.split(/\r?\n/)) {
    const line = ascii(raw.replace(/^[#*\s-]+/, '').replace(/\*\*/g, '')).trim()
    if (!line) { y -= 9; continue }
    const words = line.split(/\s+/).flatMap((word) => word.match(/.{1,80}/g) || [])
    let current = ''
    const wrapped = []
    for (const word of words) {
      const candidate = current ? `${current} ${word}` : word
      if (current && regular.widthOfTextAtSize(candidate, 10) > width) {
        wrapped.push(current)
        current = word
      } else current = candidate
    }
    if (current) wrapped.push(current)
    for (const part of wrapped) {
      if (y < margin + 16) { page = pdf.addPage(pageSize); y = pageSize[1] - margin }
      page.drawText(part, { x: margin, y, size: 10, font: regular,
                            color: rgb(0.12, 0.18, 0.22) })
      y -= 15
    }
    y -= 3
  }
  return await pdf.save()
}

async function generate(db, row) {
  const [assessment, recommendations, portfolio] = await Promise.all([
    ownedSnapshot(db, 'aura_assessments', row),
    ownedSnapshot(db, 'aura_recommendations', row),
    ownedSnapshot(db, 'aura_portfolios', row),
  ])
  const inputs = portfolio.optimization_inputs || {}
  const summarisePosition = (item) => ({
    stock_code: item.stock_code, company_name: item.company_name,
    sector: item.sector, weight: item.weight,
    investment_amount: item.investment_amount,
  })
  const facts = {
    risk_score: assessment.risk_score,
    risk_category: assessment.risk_category,
    suitability: assessment.suitability_status,
    suitability_reason: assessment.suitability_reason,
    recommended_15: (recommendations.stocks_snapshot || []).map((item) => ({
      stock_code: item.stock_code, company_name: item.company_name,
      sector: item.sector, risk_score: item.risk_score,
    })),
    selected_portfolio: (portfolio.positions || []).map(summarisePosition),
    risk_metrics: portfolio.risk_metrics,
    total_investment_for_selected_stocks: inputs.total_amount,
    currency: inputs.currency || 'EUR',
    original_optimised_portfolio: (inputs.baseline_positions || []).map(summarisePosition),
    original_risk_metrics: inputs.baseline_risk_metrics,
    allocation_mode: inputs.mode,
    methodology: portfolio.methodology_version,
  }
  const explanation = await askGemini(facts)
  const pdf = await pdfFromText(explanation)
  const path = `${row.user_id}/${row.analysis_id}/${row.report_id}.pdf`
  const { error: uploadError } = await db.storage.from(BUCKET).upload(path, pdf,
    { contentType: 'application/pdf', upsert: true })
  if (uploadError) throw uploadError
  const { data, error } = await db.from('aura_reports').update({
    status: 'completed', storage_path: path, completed_at: new Date().toISOString(),
    failed_at: null, error_code: null,
  }).eq('report_id', row.report_id).eq('status', 'processing').select('report_id')
  if (error || !data?.length) throw new Error('Could not record completed report')
  await audit(db, row, 'report_completed')
}

export async function processOnce(db) {
  await recoverInterrupted(db)
  const row = await claimNext(db)
  if (!row) return { processed: 0 }
  try {
    await generate(db, row)
    return { processed: 1, status: 'completed' }
  } catch (error) {
    console.error('Report generation failed', row.report_id, String(error))
    await db.from('aura_reports').update({
      status: 'failed', failed_at: new Date().toISOString(),
      error_code: 'generation_failed',
    }).eq('report_id', row.report_id).eq('status', 'processing')
    await audit(db, row, 'report_failed')
    return { processed: 1, status: 'failed' }
  }
}

Deno.serve(async (request) => {
  if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 })
  const expected = Deno.env.get('AURA_WORKER_TOKEN') || ''
  if (expected.length < 32) return new Response('Worker is not configured', { status: 503 })
  if (request.headers.get('x-aura-worker-token') !== expected) {
    return new Response('Unauthorized', { status: 401 })
  }
  try {
    const result = await processOnce(adminClient())
    return Response.json(result)
  } catch (error) {
    console.error('Report worker invocation failed', String(error))
    return new Response('Report worker unavailable', { status: 503 })
  }
})
