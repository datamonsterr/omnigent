# Project Context

## Purpose

Omnigent is an open-source agent framework and meta-harness: a common
orchestration layer over Claude Code, Codex, Cursor, Pi, Antigravity, and
the agents a team writes itself. Users swap or combine harnesses, enforce
policies and sandboxing, and collaborate in real time from any device.

The web app in `web/` is also the mobile app — the same `web/src` tree builds
the web, Electron, iOS, and Android clients. A UI change reaches every
client at once.

## Tech Stack

- **Backend**: Python 3.12, FastAPI/Starlette, SQLAlchemy 2.x, Alembic
  migrations, Pydantic v2.
- **Databases**: SQLite (local), PostgreSQL (server), CockroachDB (some
  deployments). One schema, dialect-portable SQL.
- **Frontend**: React 19 + TypeScript + Vite, Tailwind v4, TanStack Query,
  Radix primitives, Vitest + Testing Library.
- **Tooling**: `uv` for Python, `pnpm` for JS, `pre-commit` for gates, `just`
  for common tasks.

## Project Conventions

### Code

- Layered modules: `omnigent/<area>/` holds the domain logic,
  `omnigent/server/routes/` holds HTTP handlers, `omnigent/stores/` holds
  persistence abstractions with a SQLAlchemy implementation each.
- Every public function carries a docstring with `:param:` / `:returns:`
  lines. Comments stay short and describe the scenario, not the history.
- Database writes go through `make_named_managed_session_maker` with a
  semantic query name; never a raw session.
- New tables need an Alembic migration whose revision id follows the
  `<prefix><11 hex chars>` convention and whose header states the revision,
  what it revises, and the rollback note.

### Testing

- Unit tests beside the behaviour they cover, under `tests/`.
- Integration tests under `tests/server/integration/`; they use the real
  stores and a mock LLM.
- The web app uses Vitest and Testing Library; component tests live next to
  the component.
- Run `pre-commit run --all-files` (or `just lint`) before committing.

### Specs

Behaviour changes are specified before they are coded, in `openspec/`:

- `openspec/changes/<name>/proposal.md` — why the change exists.
- `openspec/changes/<name>/design.md` — how it is built and why.
- `openspec/changes/<name>/specs/<capability>/spec.md` — the requirements,
  each with SHALL/MUST and at least one `#### Scenario:`.
- `openspec/changes/<name>/tasks.md` — the implementation checklist.

`openspec validate <change> --strict` must pass before a change is
implemented, and the tasks are ticked off as the work lands.

## Important Constraints

- **Fork divergence**: this repository is `datamonsterr/omnigent`, a fork of
  `omnigent-ai/omnigent`. Changes here are fork-local until they are
  upstreamed. `docs/adr/` records the fork-only decisions.
- **No secrets in the database**: server-side records store paths and
  references, never tokens, passwords, or keys.
- **Harness neutrality**: the server must not hardcode one vendor's model
  names in a way that breaks another harness. Capability and policy live
  behind harness-family abstractions.
- **No runtime database migrations**: schema changes ship as Alembic
  revisions, never as runtime DDL.
