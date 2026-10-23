# Security policy

## Supported versions

Only the `main` branch and the most recent `v*` release receive security fixes. Older tags and
branches are not maintained.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
[Report a vulnerability](https://github.com/Parikshit00/grocery-deals-agent/security/advisories/new)
(repository "Security" tab, "Advisories"). Do not open a public issue, pull request or discussion
for a suspected vulnerability.

A useful report contains the affected commit or image digest, the component, the steps or input
that trigger the problem (for agent issues: the page, leaflet or prompt involved), and what an
attacker gains. You can expect an acknowledgement within 7 days and an assessment within 30 days.
Fixes are published as a GitHub security advisory, with credit unless you prefer otherwise.

This is a personal, non-commercial project maintained in spare time; there is no bug bounty.

## Scope

In scope:

- the backend, frontend and agents in this repository, including prompt injection that makes an
  agent act outside its documented limits (leave its domain allowlist, type anything other than
  the provided postcode, write data, or bypass price verification);
- the compose stack in `infra/` and the CI workflows in `.github/workflows/`;
- the published images `ghcr.io/parikshit00/grocery-deals-agent-*` and their signatures, SBOMs
  and provenance.

Out of scope:

- vulnerabilities in upstream projects (vLLM, Playwright, Langfuse, Grafana and others); please
  report those upstream;
- the retailers' websites and APIs, and OpenStreetMap Nominatim;
- findings that need an already compromised host, `.env` file or retailer website;
- denial of service by volume against a deployment that was deliberately exposed beyond a private
  network (LAN or VPN), or without the authentication the documentation calls for;
- model output quality on its own (a misread price that is flagged as unverified is a known,
  documented limitation, not a vulnerability).

Testing must stay within your own deployment. Do not run scans or injected content against the
retailers' sites, and do not test against anyone else's installation.

## Threat model

The threat model, with trust boundaries, attack scenarios for each agent, the implemented
controls, the mapping to the OWASP Top 10 for LLM Applications and the OWASP Top 10 for Agentic
Applications, and the known residual risks, is in [docs/security.md](docs/security.md).
