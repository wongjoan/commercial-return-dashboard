# Security approach — Glocomp commercial performance & ROI prototype

This prototype handles HR-sensitive data: salary and pay band, expense claims, ROI on individual cost, performance scores, HR intervention cases, and survey answers. **Access is enforced on the server.** Hiding things in the browser is only a convenience on top of that.

## 1. Sensitive-data review of the previous version (2026-10-02)

The version deployed before this upgrade was two static HTML files with no backend:

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | All 64 employees' names, base salary, bonus/commission, OTE and variable-pay split were hard-coded in **both** public pages, so anyone could read them with view-source. | Critical | **Fixed.** The pages now contain no data. Everything comes from the API after sign-in. |
| 2 | The employee page let **any visitor switch to any colleague** (dropdown or `?employee=`) and see their pay. | Critical | **Fixed.** `/api/me` always returns the signed-in user. Other people's scorecards (`/api/scorecard?id=`) are refused unless the user is in scope. |
| 3 | The employee page showed a **peer pay band** (P25 / median / P75 of colleagues' salaries in the same role). | High | **Removed** from employee views. Pay-equity analysis is Director-only. |
| 4 | No authentication at all, and the URL was publicly reachable. | Critical | **Fixed.** Signed session cookie is required for every `/api/*` call except the login endpoints. |
| 5 | HR flags (pay equity, income volatility, simulated performance) were shown next to names to every visitor. | High | **Fixed.** Pay-derived flags are Director/HR-only. Performance cases go to the employee's own management chain only. |
| 6 | Names and salaries from the client spreadsheet were committed to git and deployed. | High | **Partly fixed.** The new data uses fictional names linked to synthetic HR IDs. **The old commits still contain the original data** (see section 5). |
| 7 | HR case notes were persisted by CSV import, so any edited CSV could rewrite case state. | Medium | **Fixed.** Cases are stored server-side with stage rules. CSV is export-only. |

## 2. Controls now in place

### Authentication (`api/_lib/auth.py`)
- Session = HMAC-SHA256-signed token in a cookie that is `HttpOnly`, `SameSite=Strict`, `Secure` (on HTTPS), with an 8-hour expiry. The token carries only the user ID and expiry.
- **Role and scope are re-read from the server-side directory on every request.** Editing the cookie, the URL or JavaScript state cannot raise privileges. A forged or tampered token is rejected (covered by tests).
- Prototype sign-in = **shared demo access code + persona choice**. In production, `SESSION_SECRET` and `DEMO_ACCESS_CODE` must be set as Vercel environment variables. If they are missing, the API refuses to sign anyone in (fail closed).
- Login attempts are throttled (10 per 5 minutes per IP, per function instance).

### Role-based authorisation (`api/_lib/policy.py`)
- **Row scope:** Director → everyone. Senior Manager → their departments. Manager → self + direct reports. Employee → self.
- **Field scope:**

| Field / data | Director | Senior Manager | Manager | Employee |
|---|---|---|---|---|
| Salary, bonus/commission, OTE, pay band, pay flags | ✔ | ✘ | ✘ | ✘ |
| Individual expense claims, individual cost & ROI | ✔ | ✘ | ✘ | ✘ |
| Aggregated cost/ROI (groups of 5 or more) | ✔ | own depts | ✘ | ✘ |
| Individual scorecards, KPI gaps | all | own depts | own team | self |
| HR cases | all + exec decision | own depts | own team | ✘ (sees "improvement focus" only) |
| Individual pulse/onboarding survey answers | ✔ (HR review) | ✘ | ✘ | ✘ |
| Team survey themes (n ≥ 5) | ✔ | ✔ | ✔ | ✘ |

- Aggregates are suppressed below **5 people**, so a single person's pay or survey answer can't be inferred from a small group. A **differencing guard** hides team rows whenever "department total − team totals" would leave a group of 1–4 people.
- Errors for out-of-scope IDs look the same whether or not the ID exists, so IDs can't be enumerated.

### Who can change KPI weightages
- **Role templates** (default financial/non-financial split + KPI weights for a role): the **Director** (any role) and **Senior Managers** (roles in their departments).
- **Individual agreements** (a person's agreed split and targets): the employee's **Manager**, **Senior Manager** or the **Director**. Managers must stay within **±15 points** of the role template's financial weight. Larger changes need a senior manager.
- **Employees can view** their weightage but cannot change it.
- Every change requires a written reason. It is validated (weights 0–100, each bucket sums to 100, KPIs must belong to the role's library) and written to the audit log.

### API protection (`api/_lib/router.py`, `api/app.py`, `vercel.json`)
- Every endpoint authenticates, then authorises, before reading data. Responses are **built field by field** from the policy, so restricted fields are never sent and then hidden.
- POST requests must be `application/json`, and a cross-origin `Origin` is refused (CSRF defence in depth on top of SameSite=Strict).
- Request bodies over 64 KB are rejected. Inputs are type- and range-checked. Free text is length-capped and HTML-escaped when rendered.
- `Cache-Control: no-store` on all API responses. Security headers: CSP, `X-Frame-Options: DENY`, `nosniff`, HSTS, `Referrer-Policy: no-referrer`.
- `seed.json` and all non-public folders (`/api/_lib`, `/data`, `/scripts`, `/tests`, `/supabase`) are blocked by redirects in `vercel.json`. `.vercelignore` keeps raw datasets, tests and tools out of the deployment entirely.
- Function logs record method and path only, never cookies or bodies.

### Database access control
- **Prototype:** no database. The read-only seed is bundled inside the function, and mutable state is a JSON file written atomically.
- **Target:** `supabase/schema.sql` defines row-level security that mirrors the API policy. Pay, expense and survey tables are readable only by the Director/HR role and are writable only by the integration's service role. Senior managers get aggregates through security-barrier views with n ≥ 5. The service-role key lives only in server-side integration jobs and never in the browser.

### Secure handling of HR data
- **Data minimisation:** fictional names, synthetic HR IDs, and only the fields each view needs.
- **AI:** the KPI-suggestion engine receives KPI history, the KPI library and peer medians only — **never pay, expense or survey data**. Its output cannot change anyone's evaluation until a manager approves it.
- **Audit log:** sign-ins, weightage changes, AI generate/accept/reject/apply, HR case actions, and Director reads of expense claims.
- The raw synthetic dataset is git-ignored and never deployed. Only the derived seed is.

Run the access-control tests with `python -m unittest discover -s tests -v`. There are 18 tests covering tampered cookies, cross-role reads, URL tampering, weightage permissions, AI approval and HR escalation rules.

## 3. Remaining limitations (prototype)

1. **Demo sign-in is not identity.** Anyone with the access code can pick any persona, including the Director. Treat the code like a password, share it only with demo audiences, and rotate it after demos. Production must use Supabase Auth or company SSO (Azure AD / Google) with MFA.
2. **State is not durable on Vercel.** `/tmp` is per-instance and ephemeral, so weightage changes, cases and the audit log can reset on a cold start or differ between instances. This is acceptable for a demo, but not for real HR records. Supabase is the fix.
3. **RLS is designed but not deployed.** Until Supabase is live, the API is the only enforcement layer.
4. **Throttling is per instance and in memory.** Use Vercel firewall rules or an edge rate limiter in production.
5. **The CSP allows `'unsafe-inline'`** because the dashboard uses inline scripts (inherited structure). Next step: move scripts to files and use nonces.
6. **Anyone with Vercel project access can read `seed.json`** inside the deployment. Restrict project membership, and in production keep data in the database, not the bundle.
7. **No field-level encryption** of pay data at rest beyond the platform's disk encryption.

## 4. Before using real HR data
SSO + MFA · Supabase with RLS and PITR backups · data-processing agreement and PDPA review · retention schedule for HR cases and surveys · penetration test · remove the demo access-code login.

## 5. Action needed on the previous deployment
The earlier commits in `wongjoan/commercial-return-dashboard` (`89a06a3`, `f306d3d`) and any Vercel deployment built from them still contain the original roster names and salaries. Recommended:
1. Take down or password-protect the old deployment (Vercel → Settings → Deployment Protection).
2. Keep the GitHub repository private.
3. If the names are real people, rewrite the history (e.g. `git filter-repo`) or start a fresh repository.

A backup of the pre-upgrade version is at `C:\Users\joann\glocomp_backups\commercial-return-dashboard_2026-10-02_pre-upgrade`. It also contains that data, so keep it private.
