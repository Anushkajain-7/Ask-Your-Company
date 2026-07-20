# ADR-003: Workspace-scoped multi-tenancy as the permissions model

## Context
The original problem statement asks for document-level ACLs within a single
company's knowledge base (some employees can see HR contracts, others
can't). This project generalizes one level further: it's built so *any*
company can sign up and get their own isolated "ask the company anything"
instance, not just a single demo tenant. That reframes the permission
question as: how do we guarantee Company A never sees Company B's data,
while leaving room to add finer-grained per-document ACLs later?

## Decision
Every row that holds tenant data (`Source`, `Document`, `Chunk`, `QueryLog`)
carries a `workspace_id` foreign key. Every read path — retrieval, source
listing, audit log — filters on `workspace_id` derived from the
authenticated user's JWT, at the ORM query level, not in application logic
after the fact. There is no code path that fetches chunks without this
filter (see `retrieval.py::retrieve`, which joins and filters before any
scoring happens).

Retrieval also applies source-level role visibility in the same centralized
query: chunks are only scored if their source is visible to the current
user's role. ADR-005 documents that finer-grained within-workspace ACL layer.

The first user to sign up under a new workspace name becomes that
workspace's `admin`; all other users created later (a "invite teammate"
flow) would default to `member`. `User.role` is a string label, so workspaces
can use custom roles such as `hr` without adding a separate teams table.

## Consequences
**Positive:**
- Tenant isolation is enforced at a single, auditable layer (the retrieval
  query), not scattered across the codebase — easy to verify and easy to
  test (`tests/test_basic.py::test_tenant_isolation` asserts this directly).
- The same mechanism that isolates companies from each other can be reused
  to isolate teams/roles within a company later, without a redesign.

**Negative / trade-offs:**
- Role-gated ACLs are source-level, not per-file or per-user grants. If one
  source mixes public and restricted documents, those documents need to be
  split into separate sources with different `visible_to_roles` labels.
- Row-level filtering in application code (rather than, say, Postgres
  row-level security policies) means a future bug in a new endpoint could
  in principle forget the filter. Mitigated today by centralizing all
  retrieval through one function; a stronger long-term fix is enabling
  Postgres RLS once this moves off SQLite.

## Alternatives considered
- **Separate database per tenant**: strongest isolation guarantee, but
  operationally heavy (migrations × N databases) for an early-stage
  product signing up new companies self-serve. Revisit if a customer's
  compliance requirements demand it.
- **Document-level ACL from day one**: the "more correct" answer per the
  original problem statement, but adds a permissions model (roles, ACL
  rows, ACL-aware retrieval filtering) before the core hybrid-retrieval
  product even works end-to-end. It was deferred for the alpha build, then
  added as source-level role visibility in ADR-005.
