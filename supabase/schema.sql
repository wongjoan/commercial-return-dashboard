-- Glocomp commercial performance — TARGET Supabase/Postgres schema with row-level security.
--
-- STATUS: design target for the production migration. The current prototype does NOT use Supabase (verified:
-- the deployed app is static HTML + one Vercel Python function using a JSON seed and a local/tmp state file).
-- This file has not been applied to a live Supabase project; review and test it before use.
--
-- Principle: the API remains the primary gatekeeper (see api/_lib/policy.py), and RLS is defence in depth:
-- even a leaked anon key or a bug in an endpoint cannot read rows the signed-in user's role may not see.
-- Sensitive tables (compensation, expense_claims, survey_responses) have NO client policies except for the
-- Director/HR role; aggregates reach senior managers only through security-definer views with n >= 5.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- organisation
create table departments (
  name text primary key,
  ngp_attributed_annual numeric not null default 0
);

create type org_level as enum ('employee', 'manager', 'senior_manager', 'director');

create table employees (
  id text primary key,                         -- HRIS employee id (e.g. Workday worker id)
  auth_user_id uuid unique references auth.users(id),
  display_name text not null,
  title text not null,
  dept text references departments(name),
  level org_level not null default 'employee',
  manager_id text references employees(id),
  scope_depts text[] not null default '{}',    -- senior managers / director
  active boolean not null default true
);
create index on employees(manager_id);
create index on employees(dept);

-- ---------------------------------------------------------------- sensitive: pay & cost (Director/HR only)
create table compensation (
  employee_id text primary key references employees(id),
  pay_structure text not null check (pay_structure in ('Bonus', 'Commission')),
  base_annual numeric not null,
  variable_annual numeric not null,
  effective_from date not null default current_date
);

create table other_employment_costs (
  employee_id text primary key references employees(id),
  employer_statutory_annual numeric not null,
  benefits_annual numeric not null,
  tools_licences_annual numeric not null
);

create table expense_claims (
  id text primary key,
  employee_id text not null references employees(id),
  category text not null check (category in ('Client entertainment', 'Travel', 'Other sales expenses', 'Training')),
  claim_date date not null,
  amount numeric not null check (amount >= 0),
  status text not null check (status in ('approved', 'pending', 'rejected'))
);
create index on expense_claims(employee_id, claim_date);

-- ---------------------------------------------------------------- KPIs & weightage
create table kpi_library (
  dept text references departments(name),
  key text,
  name text not null,
  bucket text not null check (bucket in ('financial', 'non_financial')),
  unit text not null,
  direction text not null check (direction in ('higher', 'lower')),
  formula text not null,
  primary key (dept, key)
);

create table role_templates (
  role_key text primary key,                   -- "<dept>/<title>"
  dept text not null references departments(name),
  financial_weight int not null check (financial_weight between 0 and 100),
  kpi_weights jsonb not null,                  -- {"financial": {"revenue": 50, ...}, "non_financial": {...}}
  updated_by text references employees(id),
  updated_at timestamptz not null default now()
);

create table employee_kpi_agreements (
  employee_id text primary key references employees(id),
  financial_weight int not null check (financial_weight between 0 and 100),
  kpi_weights jsonb not null,
  targets jsonb not null default '{}',
  kpi_sources jsonb not null default '{}',
  agreed_by text not null references employees(id),
  agreed_at timestamptz not null default now(),
  reason text not null
);

create table kpi_actuals (
  employee_id text references employees(id),
  kpi_key text not null,
  period text not null,                        -- e.g. 2026-Q3
  target numeric not null,
  actual numeric not null,
  source_system text,                          -- CRM / ERP / PSA / survey tool
  loaded_at timestamptz not null default now(),
  primary key (employee_id, kpi_key, period)
);

create table attributed_contribution (
  employee_id text references employees(id),
  period text not null,
  ngp numeric not null,
  revenue numeric,
  basis text not null,
  primary key (employee_id, period)
);

-- ---------------------------------------------------------------- AI suggestions, HR, surveys, audit
create table ai_kpi_suggestions (
  id text primary key,
  employee_id text not null references employees(id),
  type text not null check (type in ('new_kpi', 'retarget', 'reweight')),
  kpi_key text not null,
  suggested_target numeric,
  suggested_weight int,
  reason text not null,
  evidence jsonb not null default '[]',
  engine text not null,
  status text not null default 'pending_review' check (status in ('pending_review', 'accepted', 'rejected', 'applied')),
  edited boolean not null default false,
  decided_by text references employees(id),
  created_at timestamptz not null default now()
);

create table hr_cases (
  employee_id text primary key references employees(id),
  stage text not null check (stage in ('flagged', 'manager_review', 'senior_manager', 'exec_hr_review', 'closed')),
  action_plan text not null default '',
  review_date date,
  owner text not null default '',
  final_decision text,
  opened_at timestamptz not null default now()
);

create table hr_case_events (
  id bigserial primary key,
  employee_id text not null references hr_cases(employee_id),
  actor_id text not null references employees(id),
  action text not null,
  from_stage text, to_stage text, note text,
  at timestamptz not null default now()
);

create table survey_responses (                -- HR-confidential, individual level
  employee_id text references employees(id),
  wave text not null,
  survey_date date not null,
  engagement numeric, manager_support numeric, workload numeric, career_growth numeric, belonging numeric,
  comment text,
  primary key (employee_id, wave)
);

create table audit_log (
  id bigserial primary key,
  at timestamptz not null default now(),
  actor_id text references employees(id),
  action text not null,
  target text,
  detail jsonb
);

-- ---------------------------------------------------------------- authorisation helpers
create or replace function me() returns employees
language sql stable security definer set search_path = public as $$
  select * from employees where auth_user_id = auth.uid() and active
$$;

create or replace function my_level() returns org_level
language sql stable security definer set search_path = public as $$ select (me()).level $$;

create or replace function can_view_employee(target text) returns boolean
language sql stable security definer set search_path = public as $$
  select case (me()).level
    when 'director' then true
    when 'senior_manager' then exists (select 1 from employees e where e.id = target and e.dept = any((me()).scope_depts))
    when 'manager' then target = (me()).id or exists (select 1 from employees e where e.id = target and e.manager_id = (me()).id)
    else target = (me()).id
  end
$$;

create or replace function manages(target text) returns boolean
language sql stable security definer set search_path = public as $$
  select target <> (me()).id and (me()).level <> 'employee' and can_view_employee(target)
$$;

-- ---------------------------------------------------------------- row-level security
alter table employees enable row level security;
alter table compensation enable row level security;
alter table other_employment_costs enable row level security;
alter table expense_claims enable row level security;
alter table kpi_library enable row level security;
alter table role_templates enable row level security;
alter table employee_kpi_agreements enable row level security;
alter table kpi_actuals enable row level security;
alter table attributed_contribution enable row level security;
alter table ai_kpi_suggestions enable row level security;
alter table hr_cases enable row level security;
alter table hr_case_events enable row level security;
alter table survey_responses enable row level security;
alter table audit_log enable row level security;

create policy employees_read on employees for select using (can_view_employee(id));

-- Pay, cost and expense line items: Director/HR only. No insert/update policies — loaded by the service role
-- from the HRIS / finance integration, never written from the browser.
create policy comp_director on compensation for select using (my_level() = 'director');
create policy oec_director on other_employment_costs for select using (my_level() = 'director');
create policy claims_director on expense_claims for select using (my_level() = 'director');
create policy survey_hr on survey_responses for select using (my_level() = 'director');

create policy lib_read on kpi_library for select using (auth.uid() is not null);
create policy tpl_read on role_templates for select using (auth.uid() is not null);
create policy tpl_write on role_templates for update using (
  my_level() = 'director' or (my_level() = 'senior_manager' and dept = any((me()).scope_depts))
);

create policy agree_read on employee_kpi_agreements for select using (can_view_employee(employee_id));
create policy agree_write on employee_kpi_agreements for all using (manages(employee_id)) with check (manages(employee_id));
-- The ±15-point manager band and "buckets sum to 100" rules are enforced by the API and should also be a trigger.

create policy actuals_read on kpi_actuals for select using (can_view_employee(employee_id));
create policy contrib_read on attributed_contribution for select using (can_view_employee(employee_id));

create policy ai_read on ai_kpi_suggestions for select using (manages(employee_id));
create policy ai_write on ai_kpi_suggestions for update using (manages(employee_id)) with check (manages(employee_id));

create policy hr_read on hr_cases for select using (manages(employee_id));
create policy hr_events_read on hr_case_events for select using (manages(employee_id));
-- Stage transitions go through the API (api/_lib/hr.py allowed_actions) using the service role, so no client write policy.

create policy audit_read on audit_log for select using (my_level() = 'director' or actor_id = (me()).id);

-- ---------------------------------------------------------------- aggregate views for senior managers (n >= 5)
create or replace view v_department_cost_quarter with (security_barrier) as
select e.dept,
       count(*) as headcount,
       sum(c.base_annual) / 4 as salary_cost,
       sum(o.employer_statutory_annual + o.benefits_annual + o.tools_licences_annual) / 4 as other_cost
from employees e
join compensation c on c.employee_id = e.id
join other_employment_costs o on o.employee_id = e.id
where e.active
group by e.dept
having count(*) >= 5;
-- Expose through a security-definer RPC that filters to (me()).scope_depts; never grant direct select to clients.
