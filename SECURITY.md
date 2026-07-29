# Security Policy

## Supported Versions

Only the latest release receives security updates.

| Version | Supported          |
|---------|--------------------|
| latest  | :white_check_mark: |
| < latest| :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public issue.

Instead, report it privately:

1. Go to the [Security Advisories](https://github.com/zcbacxc/movie-narrator/security/advisories/new) tab
2. Click "Report a vulnerability"
3. Provide a clear description and steps to reproduce

You can also email: zcbacxc@users.noreply.github.com

### Response timeline

- **Acknowledgement**: within 48 hours
- **Initial assessment**: within 1 week
- **Fix or mitigation**: target 2 weeks for critical issues

## Scope

This policy covers the `movie-narrator` core engine package. For the Web UI package ([movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)), report issues in that repository.

## Out of scope

- API key leakage in user configurations (user responsibility)
- Third-party provider vulnerabilities (OpenAI, Edge-TTS, TMDB, etc.)
- Issues in optional dependencies installed via `[media]`, `[ml]`, or `[full]` extras
