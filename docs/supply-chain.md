# Supply chain

The backend and frontend images are built by CI from the same Dockerfiles and targets as
`infra/docker-compose.yml` (`infra/backend.Dockerfile` target `runtime`, `infra/frontend.Dockerfile`,
build context the repository root). The workflow is
[`.github/workflows/supply-chain.yml`](https://github.com/Parikshit00/grocery-deals-agent/blob/main/.github/workflows/supply-chain.yml).

## What is produced

| When | Output | Where |
|---|---|---|
| Every pull request | CycloneDX JSON SBOM per image (images are built, not pushed) | Workflow run artifacts `sbom-backend.cdx.json`, `sbom-frontend.cdx.json` |
| Push to `main`, `v*` tags, manual run | Image | `ghcr.io/parikshit00/grocery-deals-agent-backend`, `ghcr.io/parikshit00/grocery-deals-agent-frontend` |
| same | CycloneDX SBOM of the pushed digest | Run artifacts, plus a signed SBOM attestation |
| same | SLSA v1 build provenance (which repo, workflow, commit and runner built the digest) | Signed attestation |
| same | Keyless cosign signature of the digest | GHCR, next to the image; entry in the Rekor transparency log |

Image tags: `sha-<short commit>` always, the branch name (`main`) for branch builds, and `1.2.3` plus
`1.2` for a `v1.2.3` tag. Attestations are stored in GitHub (repository "Attestations" page) and,
with `push-to-registry`, also in GHCR next to the image.

Nothing here gates a pull request: the SBOM job only fails if the image does not build, which the
`compose` check in `ci.yml` already covers. Vulnerability gating on the SBOM is a separate decision
and is not part of this workflow.

## Why

An image on its own says nothing about what is inside it or where it came from. By 2026 three
artifacts are the expected baseline for anything shipped as a container:

- **SBOM** (software bill of materials): the inventory of OS packages and language libraries in
  the image. It answers "are we affected by CVE X" without pulling and scanning the image again.
  The EU Cyber Resilience Act requires manufacturers to draw up an SBOM for products with digital
  elements (vulnerability reporting applies from September 2026, the full obligations from
  December 2027), and US federal software procurement asks for one as well.
- **Build provenance** ([SLSA](https://slsa.dev/spec/v1.0/) v1): a signed statement that a given
  digest was built by this repository's workflow from a specific commit on a GitHub-hosted runner.
  It rules out an image that was built on a laptop and pushed by hand. GitHub artifact attestations
  from a hosted runner meet SLSA Build Level 2.
- **Signature** ([Sigstore](https://www.sigstore.dev/) cosign, keyless): no long-lived signing
  key exists. The job's GitHub OIDC token is exchanged for a short-lived Fulcio certificate that
  names the workflow and ref, and the signature is recorded in the public Rekor log. A consumer
  verifies the identity of the signer instead of trusting a key file.

The base images in both Dockerfiles and every third-party image in compose are already pinned by
digest, and every action in the workflow is pinned by commit SHA, so the inputs to the build are
fixed as well. The vLLM image (`models` profile) is built locally on the GPU server and is not
published.

## Verify

Requirements: [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) v2 or newer,
a recent [GitHub CLI](https://cli.github.com/) (`gh attestation`), `jq`. Pulling and the `gh`
commands need access to the packages (public packages need no login for cosign and docker; `gh`
always needs `gh auth login`).

```bash
IMAGE=ghcr.io/parikshit00/grocery-deals-agent-backend   # or ...-frontend
TAG=main                                               # or sha-1a2b3c4, 1.2.3
```

### Signature

Images built from `main`:

```bash
cosign verify "$IMAGE:$TAG" \
  --certificate-identity "https://github.com/Parikshit00/grocery-deals-agent/.github/workflows/supply-chain.yml@refs/heads/main" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com | jq .
```

Release tags (`v*`):

```bash
cosign verify "$IMAGE:1.2.3" \
  --certificate-identity-regexp '^https://github\.com/Parikshit00/grocery-deals-agent/\.github/workflows/supply-chain\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com | jq .
```

cosign resolves the tag to a digest and verifies that digest. To deploy exactly what was
verified, pin the digest:

```bash
docker pull "$IMAGE:$TAG"
docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE:$TAG"   # ghcr.io/...@sha256:...
```

### Build provenance

```bash
gh attestation verify "oci://$IMAGE:$TAG" \
  --repo Parikshit00/grocery-deals-agent \
  --signer-workflow Parikshit00/grocery-deals-agent/.github/workflows/supply-chain.yml
```

The default predicate type is SLSA provenance v1. Add `--format json` to see the commit, workflow
ref and runner recorded in the statement.

### SBOM attestation

```bash
gh attestation verify "oci://$IMAGE:$TAG" \
  --repo Parikshit00/grocery-deals-agent \
  --predicate-type https://cyclonedx.org/bom
```

Extract the verified SBOM from the attestation:

```bash
gh attestation verify "oci://$IMAGE:$TAG" \
  --repo Parikshit00/grocery-deals-agent \
  --predicate-type https://cyclonedx.org/bom \
  --format json --jq '.[0].verificationResult.statement.predicate' > sbom.cdx.json
```

Or download the plain SBOM from a workflow run (pull requests included):

```bash
gh run download <run-id> --repo Parikshit00/grocery-deals-agent --name sbom-backend.cdx.json
```

### Inspect the SBOM

```bash
jq '.metadata.component.name, (.components | length)' sbom.cdx.json      # image, component count
jq -r '.components[] | "\(.type)\t\(.name)\t\(.version)"' sbom.cdx.json | sort | less
jq -r '.components[] | select(.name == "openai") | .version' sbom.cdx.json # one package
grype sbom:sbom.cdx.json                                                     # known CVEs, offline from the image
```

## One-time repository setup

- **Package permissions.** The workflow pushes with `GITHUB_TOKEN` (`packages: write`). The first
  push creates the packages and links them to the repository through the
  `org.opencontainers.image.source` label. If a package with the same name already exists and is
  not linked, grant the repository write access in the package settings ("Manage Actions access").
- **Visibility.** New GHCR packages are private. They are public only after the owner sets each
  package to public in its settings; until then `docker pull` and `cosign verify` need
  `docker login ghcr.io`.
- **Attestations** are free for public repositories; a private repository needs GitHub Enterprise
  Cloud.
