# AURA · Streamlit Community Cloud + Supabase

本版只需要 Streamlit Community Cloud、原有的 Supabase 项目和 Gemini API。Python 风险评估及组合优化在 Streamlit 的服务端执行；Supabase Auth 管理账户，数据库保存历史，Supabase Edge Function 和 Cron 异步生成报告。没有独立 Web API 服务。

## 客户流程

客户注册/登录 → 完成 10 问风险评估 → 从真实 `stock_metrics_excel` 筛出 15 股 → 选 3–15 股并输入投资金额/币种 → Python 计算权重和风险指标 → 保存本次 `analysis_id` 关联记录 → 在 `My Investment History` 查看 → 请求 Gemini 报告 → `My Reports` 等待并下载私有 PDF。

组合权重合计所选股票投资金额的 100%；没有固定 40% equity allocation。Gemini 只解释已保存的 Python 结果，不重新选股或改权重。客户手动调整金额后的前后对比仍保留。

## 1. 核对 Supabase

目标项目 ref 是 `mlcqlxuiajwcorswwtdf`。先在该项目 SQL Editor 运行只读的 `database/verify_aura.sql`。六张 `aura_*` 表经 API 确认存在；本地没有权限核实全部 RLS 策略、私有 Storage bucket 和 3–15 股约束。若缺少表、策略或 bucket，审阅后应用 `database/20261017_aura_records.sql`。如果组合表仍只允许 3–4 股，应用 `database/20261017_aura_portfolio_3_to_15.sql`。不要在其他项目运行这些脚本。

## 2. 部署 Streamlit

把本目录作为 GitHub 仓库内容，Streamlit Community Cloud 入口为根目录 `app.py`，依赖文件为 `requirements.txt`。在 Cloud 的 Secrets 中设置：

```toml
AURA_DEMO_MODE = "0"
SUPABASE_URL = "https://mlcqlxuiajwcorswwtdf.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "<original-project-publishable-key>"
SUPABASE_SECRET_KEY = "<server-only-secret-key>"
AURA_PUBLIC_URL = "https://<your-app>.streamlit.app"
```

`SUPABASE_SECRET_KEY` 只放在 Streamlit Cloud Secrets，不能放进 GitHub、网页或浏览器变量。每次客户操作时，Streamlit 先用 Supabase Auth 验证该客户的 JWT，然后才以服务端密钥写入其对应记录。Supabase Auth 的 Site URL 和 Redirect URLs 需设为最终的 Streamlit URL。`AURA_DEMO_MODE=1` 只用于设计预览，不保存客户历史，也不生成报告。

密码重置还需要在 Supabase **Authentication → Email Templates → Reset password** 中，将邮件的操作链接设为以下形式，把域名换成最终的 Streamlit 地址：

```html
<a href="https://<your-app>.streamlit.app/?token_hash={{ .TokenHash }}&type=recovery">Reset password</a>
```

默认模板的确认链接会把会话放在 URL 片段中，Streamlit 服务端无法读取；这里改用 `token_hash`，应用会向 Supabase 验证后让客户设置新密码。注册确认邮件可继续使用默认模板；确认后客户回到登录页输入邮箱和密码。

## 3. 部署报告 Edge Function

在同一 Supabase 项目部署 `supabase/functions/aura-report-worker/index.js`。从本目录执行以下命令；使用支持 JavaScript `entrypoint` 的 Supabase CLI 1.215.0 或更高版本。若 CLI 作为项目依赖安装，把命令中的 `supabase` 改为 `npx supabase`。

```sh
supabase login
supabase link --project-ref mlcqlxuiajwcorswwtdf
supabase functions deploy aura-report-worker --use-api
```

`supabase/config.toml` 为该函数关闭网关 JWT 校验；**函数自身**必须验证高强度的 `AURA_WORKER_TOKEN`，因此该 token 不能放在 GitHub。

在 Supabase Edge Function Secrets 中设置 `GEMINI_API_KEY`、`GEMINI_MODEL` 和随机的 `AURA_WORKER_TOKEN`（至少 32 个字符）。Supabase 会自动注入 `SUPABASE_URL` 和服务端密钥集合。该函数每次只处理一个待生成报告，90 秒内等待 Gemini，生成英文 PDF 并上传到私有 `aura-reports` bucket。状态为 `pending → processing → completed`，失败则标记 `failed`，客户可重试。

在 Supabase Vault 保存两个值：`aura_worker_token`（与 Edge Function 的 `AURA_WORKER_TOKEN` 相同）和 `aura_publishable_key`（原项目 publishable key）。然后审阅并运行 `database/20261018_aura_report_cron.sql`，每分钟触发一次函数。`pg_net` 的超时设为 120 秒；Supabase 免费方案 Edge Function 的运行时间上限为 150 秒，实际生成时间仍取决于 Gemini 响应。不要把 token 或 Gemini key 填进 SQL 文件。

## 4. 验证

用两个测试账户分别登录。账户 A 完成评估，检查 15 股快照、3 股和 15 股组合、金额和币种在 History 中保存；请求报告，检查状态变化和 PDF 下载。账户 B 不应看到 A 的记录，也不能通过改 `analysis_id` 或 `report_id` 获取 A 的文件。断开 Gemini 或使用无效模型时，任务应显示失败，恢复配置后可以重试。

这份包尚未部署，也未写入线上 Supabase；本地测试只能验证代码路径和模拟报告。需要在目标项目配置 Secrets、部署 Edge Function、运行 SQL 并完成双账户线上测试后，才能对客户开放。
