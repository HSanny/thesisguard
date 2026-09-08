# Security Policy

ThesisGuard handles market data, portfolio configuration, alert logic, and potentially sensitive deployment credentials. Security issues should be treated as confidential until they are remediated.

## Supported versions

Until public releases are introduced, only the current `main` branch is considered supported.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a suspected security vulnerability.

Use one of the following private channels:

1. GitHub Private Vulnerability Reporting / Security Advisories for this repository, if enabled.
2. Contact the repository owner privately through the GitHub account associated with `HSanny/thesisguard`.

Include, where possible:

- affected component or endpoint;
- reproduction steps;
- impact assessment;
- proof of concept that does not expose third-party data;
- suggested mitigation, if known.

## Secrets and credentials

Never commit:

- exchange API keys or secrets;
- Railway/PostgreSQL credentials;
- Telegram bot tokens;
- OAuth credentials;
- JWT signing keys;
- private webhook secrets;
- production `.env` files.

Secrets must be injected through the deployment platform's encrypted environment-variable system.

## Trading and execution boundary

ThesisGuard is currently a monitoring and decision-support system. Any future capability that can place, modify, or cancel trades must be isolated behind explicit permissions, independent authentication, audit logging, and kill-switch controls.

## Data exposure

Portfolio positions, cost bases, leverage, alerts, decision journals, and user-specific thesis settings should be treated as confidential user data. Production deployments must not expose raw database credentials or unrestricted administrative endpoints to the public internet.

## Dependency security

CI should run dependency-license checks and, as the project matures, dependency-vulnerability scanning. High-risk new dependencies should not be merged without review of both their security posture and license terms.

## Disclosure

We aim to validate reports promptly, remediate confirmed vulnerabilities, and coordinate disclosure after a fix is available. No promise of a specific response time is made at this stage.
