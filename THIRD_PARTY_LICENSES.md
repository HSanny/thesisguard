# Third-Party License Register

ThesisGuard itself is proprietary. Third-party dependencies remain governed by
their own licenses; this repository does **not** relicense third-party code.

This file records the direct Python dependencies currently pinned in
`backend/requirements.txt`. Transitive dependencies are scanned automatically
in CI and must be reviewed when the dependency graph changes.

| Package | Current pinned version | License family | Commercial-use note |
| --- | ---: | --- | --- |
| FastAPI | 0.116.1 | MIT | Permissive |
| Uvicorn | 0.35.0 | BSD-3-Clause | Permissive |
| HTTPX | 0.28.1 | BSD-3-Clause | Permissive |
| websockets | 15.0.1 | BSD-3-Clause | Permissive |
| Pydantic | 2.11.7 | MIT | Permissive |
| pydantic-settings | 2.10.1 | MIT | Permissive |
| PyYAML | 6.0.2 | MIT | Permissive |
| SQLAlchemy | 2.0.43 | MIT | Permissive |
| pg8000 | 1.31.5 | BSD-3-Clause | Permissive PostgreSQL driver |
| pytest | 8.4.1 | MIT | Development/test dependency |

## Policy

The current dependency policy is intentionally conservative:

- MIT, BSD, Apache-2.0, ISC, PSF and similarly permissive licenses are normally acceptable.
- LGPL and MPL dependencies require explicit human review and preservation of applicable obligations.
- AGPL, strong GPL, SSPL, Business Source License, Elastic License, Commons Clause,
  non-commercial-only terms, and similarly restrictive/source-available terms are
  blocked by default until explicitly reviewed.
- Unknown or ambiguous license metadata must be surfaced for human review.
- The upstream license text is authoritative when package metadata and license
  files disagree.

A notable early audit finding was that Psycopg 3 is LGPL-3.0-only. It is
commercially usable under its terms, but ThesisGuard moved to the BSD-3-Clause
`pg8000` driver in the Railway deployment candidate to reduce compliance
complexity for a proprietary SaaS product.

## Automated audit

CI installs `pip-licenses`, scans the complete installed Python dependency tree,
runs `scripts/license_guard.py`, and uploads a dependency license report as a
workflow artifact.

The automated policy is an engineering guardrail, not a substitute for legal
review before commercial launch, packaged distribution, enterprise licensing,
or a material change in the dependency graph.
