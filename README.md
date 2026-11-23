# Ship it: Python to AWS

A small Python service packaged with Docker, with GitHub Actions delivery to
**private ECR → Lightsail Containers** in **eu-west-2 (London)**. Terraform defines
the infrastructure; GitHub OIDC provides temporary AWS credentials.

**Status:** Service, infrastructure code, release, and rollback workflows are
implemented. AWS provisioning and a live deployment have not yet been verified.
Releases currently run manually; automatic delivery on every code change remains
a gap against the interview brief.

## Interviewer walkthrough

1. **Service:** Read [hello_world.py](hello_world.py). It prints its version or
   serves JSON through Gunicorn on port `8080`.
2. **Container:** Read [Dockerfile](Dockerfile). The image uses a digest-pinned
   Python base, hash-verified dependencies, a non-root user, and a health check.
3. **Delivery:** Read [ship.yml](.github/workflows/ship.yml). Tests → build →
   container smoke test → ECR upload → Lightsail deployment → live verification.
4. **Infrastructure:** Read [infra/main.tf](infra/main.tf). ECR stores immutable
   images; a one-node Nano Lightsail service runs them behind managed HTTPS.
5. **Recovery:** Read [rollback.yml](.github/workflows/rollback.yml). Restore and
   verify a previously published image without rebuilding it.

## Repository guide

| Path | Contents |
| --- | --- |
| `hello_world.py` | WSGI service and authoritative `APP_VERSION`. |
| `Dockerfile`, `.dockerignore` | Runtime image and allowed build-context files. |
| `requirements.txt` | Pinned, hash-verified Python dependencies. |
| `.github/workflows/` | Manual infrastructure/release and rollback workflows. |
| `scripts/` | `release.py`: version checks, ECR lookup, deployment and rollback; `check_service.py`: HTTP health/version verification. |
| `tests/` | Python service, versioning, deployment, rollback, and verification tests with mocked AWS calls. |
| `infra/` | Application infrastructure, IAM, variables, outputs, and S3 backend configuration. |
| `infra/tests/` | Terraform infrastructure tests using a mocked AWS provider. |
| `infra/bootstrap/` | One-time S3 state bucket, GitHub OIDC, Terraform role, and operator-policy template. |
| `infra/bootstrap/tests/` | Mocked Terraform tests for bootstrap resources. |
| `output/iam-console-guide/` | Supporting IAM policy JSON templates and HTML console setup guides. |

## Run locally

Use Python **3.13**. Printing the version and running unit tests need no third-party
dependencies or AWS access:

```sh
python3 hello_world.py
python3 -m unittest discover -s tests -v
```

For local HTTP development:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/gunicorn --bind 127.0.0.1:8080 hello_world:app
```

Or build and run with Docker:

```sh
VERSION=$(python3 -c 'from hello_world import APP_VERSION; print(APP_VERSION)')
docker build --build-arg VERSION="$VERSION" -t "mentee-test:$VERSION" .
docker run --rm -p 8080:8080 --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges "mentee-test:$VERSION"
```

In another terminal:

```sh
curl http://localhost:8080/version
curl http://localhost:8080/healthz
```

`/` and `/version` return `{"message": "Hello, world!", "version": "1.0.0"}`.
`/healthz` returns `{"status": "ok"}`. Access/error logs go to stdout/stderr.

## Versioning

Edit **`APP_VERSION` in [hello_world.py](hello_world.py)** and commit it with the
change. That single `Major.Minor.Patch` value becomes the ECR tag, OCI version
label, and runtime `/version` response. Docker rejects a mismatched build argument.

| Change | Example bump |
| --- | --- |
| Compatible bug fix, dependency fix, or base-image security update | `1.0.0 → 1.0.1` |
| Compatible feature | `1.0.1 → 1.1.0` |
| Breaking API change | `1.1.0 → 2.0.0` |

For a base-image fix, update the pinned Docker digest **and bump `APP_VERSION`**.
Versions cover the whole deliverable; the pipeline validates them rather than
choosing bumps automatically. Patch numbers have no artificial cap.

New releases must exceed the highest published version. Immutable ECR tags prevent
overwriting releases. A `packaging-<commit SHA>` alias tracks the latest commit
changing paths in `PACKAGING_PATHS` in `scripts/release.py`; matching reruns and
documentation-only changes reuse the published image. Changed packaging requires
a new version. Digests are recorded for traceability.

## Deploy to AWS

### One-time bootstrap

Use the exam account's credentials, AWS CLI, and Terraform **1.13.5** (supported:
`>= 1.10, < 2.0`). Check `aws sts get-caller-identity` and confirm the account has
`candidate-exam-boundary`. Required operator permissions are listed in
[operator-policy.json](infra/bootstrap/operator-policy.json); replace `<ACCOUNT_ID>`.

```sh
export TF_VAR_aws_account_id="YOUR_12_DIGIT_EXAM_ACCOUNT_ID"
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
terraform -chdir=infra/bootstrap apply bootstrap.tfplan
terraform -chdir=infra/bootstrap output github_repository_variables
```

If GitHub's OIDC provider already exists, set
`TF_VAR_create_github_oidc_provider=false` before planning. Keep a private backup
of the ignored local bootstrap state. Application infrastructure state uses an
encrypted, versioned S3 bucket with native locking.

Add the output values **`AWS_ACCOUNT_ID`** and **`TF_STATE_BUCKET`** under GitHub
**Settings → Secrets and variables → Actions → Variables**. Use a plain bucket
name. AWS keys and Terraform state must stay out of Git.

### Release and verify

Push the workflows to `main`, then open **Actions → Ship to Lightsail → Run workflow**
and select `main`:

| Run | Apply infrastructure | Build, upload, and deploy |
| --- | --- | --- |
| First deployment after bootstrap | On | On |
| Infrastructure change | On | Off |
| Application/image release | Off | On |

Terraform defaults off; image release defaults on. If selected Terraform fails,
release stops. Failed tests, builds, or container smoke checks stop publication.
The workflow waits for the requested Lightsail deployment to become `ACTIVE`,
confirms its image, then checks `/healthz` and `/version` over HTTPS. The run summary
contains the live URL, version, and ECR digest; open that URL's `/version` to demo it.

Terraform owns capacity and registry access; CI owns image deployments. The two
created IAM roles, `exam-github-terraform` and `exam-github-deploy`, use the required
boundary and restrict OIDC trust to this repository's `main`. Lightsail's managed
ECR puller principal receives separate repository access.

**Pushing alone does not deploy.** To satisfy automatic per-change delivery, add a
`push` trigger for `main` and update the release job condition; Terraform can stay
manual. Ship and rollback share a concurrency group and do not cancel running
operations; a newer pending request can replace an older pending request.

## Roll back

Open **Actions → Roll back Lightsail → Run workflow**, select `main`, and enter an
existing ECR version, such as `1.0.0`. Find versions in prior run summaries or
**ECR → mentee-test → Images**.

The workflow pulls and smoke-tests that image, preserves the active container and
endpoint configuration, redeploys with the selected image, and verifies it live.
It requires an active deployment and does not rebuild or change the source version.
Rollback is manual and restores the image, not historical infrastructure or data.

Both workflows retain `release-metadata.json` and, when available,
`previous-deployment.json` as artifacts for **14 days**. ECR images have no expiry
policy; keep both their version and packaging tags for reuse and rollback.

## Infrastructure checks and production next steps

With Terraform installed, these checks use mocked AWS providers and create no resources:

```sh
terraform fmt -check -recursive infra
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
terraform -chdir=infra test
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap validate
terraform -chdir=infra/bootstrap test
```

Builds reuse dependency layers and GitHub Actions BuildKit cache. Before real
traffic, add a vulnerability scan gate (ECR is configured to scan on push), monitoring
and alerts, service access controls, load tests, and tested automatic rollback.
Lightsail capacity is configured through Terraform. Blue/green needs separate
services and traffic switching; ECS Fargate is a future option for autoscaling
and more deployment control.
