# Third-Party License Register

ThesisGuard itself is proprietary. The project depends on third-party open-source packages that remain subject to their own licenses.

This file records the **direct runtime dependencies currently pinned in `backend/requirements.txt`**. Transitive dependencies are scanned automatically in CI and must be reviewed when the dependency graph changes.

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
| psycopg / psycopg-binary | 3.2.9 | LGPL-3.0 | Copyleft library license; keep it separable/unmodified as a normal dependency and preserve applicable notices/terms |
| pytest | 8.4.1 | MIT | Development/test dependency |

## Policy

The current dependency policy is:

- MIT, BSD, Apache-2.0, ISC, PSF and similarly permissive licenses are normally acceptable.
- LGPL dependencies require explicit review and preservation of applicable obligations.
- AGPL, strong GPL, SSPL, Business Source License, Elastic License, Commons Clause, and similarly restrictive/source-available terms are blocked by default until explicitly reviewed.
- Unknown or ambiguous license metadata must be surfaced for human review.

## Automated audit

CI installs `pip-licenses` and runs `scripts/license_guard.py` against the complete installed dependency tree.

The guard fails the build when it detects a license family on the project's deny list. Unknown/ambiguous metadata is printed prominently so it can be reviewed before commercial distribution.

## Important

This register is an engineering compliance aid, not a substitute for legal review. Before selling ThesisGuard, distributing packaged software, or signing an enterprise agreement, perform a formal dependency and IP review and preserve any required third-party notices.
