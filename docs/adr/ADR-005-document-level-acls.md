# ADR-005: Source-level role ACLs within a workspace

## Context
ADR-003 established the hard tenant boundary: Company A must never retrieve
Company B's chunks. The next gap is inside a workspace. A real company often
has broad sources (public wiki pages) and restricted sources (HR compensation
notes, finance planning, legal drafts). The app needs to prevent a normal
workspace member from retrieving restricted chunks while preserving the
existing centralized retrieval boundary.

The current data model already treats `Source` as the logical connector or
bucket that owns documents and chunks. That makes source-level ACLs the
smallest useful increment: restricted documents can be placed in an HR-only
source, and every chunk from that source inherits the same visibility rule.

## Decision
Add `Source.visible_to_roles`, a normalized role label:
- `all`: every active user in the workspace can retrieve from the source.
- `admin`: only admins can retrieve from the source.
- Any custom label such as `hr`: admins and users whose `User.role` exactly
  matches that label can retrieve from the source.

Keep `User.role` as a string instead of adding a separate teams table. The
existing values `admin` and `member` still work, and the same column can hold
custom labels like `hr`, `finance`, or `legal`.

Enforce ACLs only in `backend/app/services/retrieval.py::retrieve`, next to
the existing workspace filter. The ORM query still joins `Chunk`, `Document`,
and `Source`, filters by `Chunk.workspace_id`, filters to ready documents, and
then applies source visibility before BM25, dense scoring, re-ranking, answer
generation, citations, or audit logging see any candidate chunk.

Admins are treated as workspace administrators and can retrieve from every
source in their workspace. Non-admin users can retrieve from sources marked
`all` or exactly matching their role.

## Consequences
**Positive:**
- The confidentiality check lives at the same retrieval boundary as tenant
  isolation, so it is easy to audit and test.
- Existing sources remain visible because the default visibility is `all`.
- Custom roles are supported without a new membership schema or migration-heavy
  teams model.
- The frontend can set source visibility at creation time without changing the
  ingestion pipeline.

**Negative / trade-offs:**
- ACL granularity is source-level, not individual-document-level within one
  source. Teams must separate restricted documents into restricted sources.
- There is no role-management UI yet; custom roles can be assigned in the data
  model, but the current product still lacks invite/team administration flows.
- `admin` is a broad override. That is useful for workspace administration and
  auditability, but it is not a least-privilege model for organizations that
  need admins who cannot see sensitive HR or legal documents.

## Alternatives considered
- **Per-document ACL column on `Document`**: more granular, but the UI and
  ingestion flow are source-oriented today. Source-level ACLs satisfy the
  main milestone requirement with less schema and workflow churn.
- **Separate ACL join table (`source_role_grants`)**: supports many roles per
  source and cleaner normalization, but adds another table and more admin UI
  before the product has team management. A join table is the natural upgrade
  if sources need multi-role grants.
- **Filter after retrieval in the ask router**: rejected. It would risk
  scoring, logging, or citing restricted chunks before filtering. ACLs belong
  in the centralized retrieval query, before any ranking or generation.
