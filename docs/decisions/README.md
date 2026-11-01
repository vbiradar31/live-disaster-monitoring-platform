# Architecture decision records

Record architectural choices that affect multiple components, runtime behavior,
data ownership, or future maintenance. Keep implementation rationale and
trade-offs in an ADR; use source comments for local constraints and explanations.

## Naming and format

- Name each record `YYYY-MM-DD-short-decision-name.md`, using the date the
  decision was made or recorded, not an estimated historical date.
- Use one ADR per decision. Do not add dates to ordinary source comments.
- Include the sections below. Keep alternatives factual, and record only
  verification that was actually performed.
- Link affected files with repository-relative paths where practical.

## Template

```markdown
# <Decision title>

- **Date:** YYYY-MM-DD
- **Status:** Proposed | Accepted | Superseded

## Context
What problem or constraint prompted this decision?

## Decision
What approach was chosen?

## Alternatives considered
What plausible approaches were considered, and why were they not selected?

## Rationale and trade-offs
Why does the chosen approach fit? What costs or constraints does it introduce?

## Affected files
- `path/to/file`

## Verification
List commands and observed results, or state that verification was not run.
```
