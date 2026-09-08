# Security Policy

ThesisGuard processes market data, portfolio configuration, monitoring rules,
and potentially sensitive deployment credentials. Security issues should be
reported privately.

## Supported versions

Security fixes are applied to the current `main` branch and the currently
deployed production candidate/version. Experimental branches are not separately
supported unless explicitly stated.

## Reporting a vulnerability

**Do not open a public GitHub issue for a suspected vulnerability.**

Preferred reporting path:

1. Use GitHub's private Security Advisory / vulnerability-reporting workflow for
   this repository when available.
2. If that is unavailable, contact the repository owner through an authorized
   private channel and include only the minimum information required to
   reproduce the issue.

Please include:

- affected component and version/commit;
- concise reproduction steps;
- expected vs. observed behavior;
- potential impact;
- any safe proof-of-concept that does not expose third-party data.

## Sensitive information

Never include any of the following in an issue, pull request, screenshot, log,
or vulnerability report unless an explicitly approved secure channel is being
used:

- exchange API keys or secrets;
- wallet private keys, seed phrases, signing keys, or recovery material;
- GitHub, Railway, database, cloud, Telegram, or other access tokens;
- production database dumps;
- user credentials or personal account information;
- proprietary market-monitoring rules or private customer data that are not
  necessary to demonstrate the vulnerability.

If a credential is suspected to have been exposed, rotate or revoke it before
continuing investigation.

## Deployment and secret handling

Production secrets must be injected through Railway or another deployment
platform's encrypted environment-variable system. Production `.env` files,
database URLs containing credentials, API secrets, and access tokens must never
be committed to Git.

The public API/dashboard service and the private worker must use least-privilege
credentials where practical. The worker does not require a public domain.

## Security boundaries

ThesisGuard is a monitoring and decision-support product. A bad market call, a
false-positive alert, stale market data, or a missed trading opportunity is not
by itself a software-security vulnerability. However, failures that allow data
tampering, unauthorized rule changes, credential disclosure, cross-user data
access, alert forgery, or source-validation bypass are security issues.

Any future capability that can place, modify, or cancel trades must be isolated
behind explicit permissions, independent authentication, audit logging, and
kill-switch controls.

## Disclosure

Please allow the project owner to investigate and remediate a reported issue
before public disclosure. No bug bounty or payment is promised unless a
separate written program explicitly says otherwise.
