# Security policy

## Supported versions

Nautilus Studio is currently an experimental alpha. Security fixes target the
latest `main` branch until the first stable release.

## Reporting a vulnerability

Please report vulnerabilities privately to the repository maintainers instead
of opening a public issue through
[GitHub's private security advisory form](https://github.com/yeahdongcn/nautilus-studio/security/advisories/new).
Include reproduction steps, affected endpoints, and the expected impact. Do not
include real provider credentials or private media.

## Deployment boundary

The built-in server does not yet provide authentication, tenant isolation, rate
limits, or content moderation. Bind it to localhost or a trusted private network
unless it is placed behind an authenticated reverse proxy.

Treat the following as secrets:

- planner and image-provider API keys;
- model registry credentials;
- private reference images and generated videos;
- internal service URLs and topology.

Configure secrets only through environment variables or a deployment secret
store. Never persist them inside project JSON, SQLite payloads, prompts, or Git.

Server-side asset import is restricted to `STUDIO_IMPORT_ROOTS`. Review those
roots carefully and use read-only mounts where possible.
