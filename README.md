# mentee-test

A small Python service with one SemVer version shared by the app and container image.
Terraform, release, and rollback workflows are prepared for review. AWS infrastructure
has not been applied and no live deployment is claimed.

Print the version locally (Python 3.13, no dependencies required):

```sh
python3 hello_world.py
```

Local Python and the container report the same version, initially `1.0.0`.

Build and run the container:

```sh
VERSION=$(python3 -c 'from hello_world import APP_VERSION; print(APP_VERSION)')
docker build --build-arg VERSION="$VERSION" -t "mentee-test:$VERSION" .
docker run --rm --name mentee-test -p 8080:8080 --read-only --cap-drop=ALL --security-opt=no-new-privileges "mentee-test:$VERSION"
```

In another terminal, inspect the live version and health:

```sh
curl http://localhost:8080/version
curl http://localhost:8080/healthz
```

`/` and `/version` return:

```json
{"message": "Hello, world!", "version": "1.0.0"}
```

`/healthz` returns `{"status": "ok"}`. The service listens on port 8080 and
writes access and error logs to stdout/stderr. For local HTTP development:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/gunicorn --bind 127.0.0.1:8080 hello_world:app
```

## Versioning

**Change `APP_VERSION` in [`hello_world.py`](hello_world.py#L6), line 6.** This is the
only version to edit. For a major release, change:

```python
APP_VERSION = "1.0.0"
```

to:

```python
APP_VERSION = "2.0.0"
```

The pipeline reads this value and uses it for the ECR tag and Docker `VERSION`
build argument. Docker checks it against the source and writes the OCI image
version label. `/version` and `python hello_world.py` report the source value.
A mismatch fails the build; there is no independent image version or counter.
A container does not discover its registry tag at runtime: the same source version
is included in the image and used as its immutable publication tag.

Developers choose the bump using [SemVer](https://semver.org/):

| Change | App and image version |
| --- | --- |
| Initial release | `1.0.0` |
| Compatible bug fix, dependency fix, or pinned base-image security update | `1.0.1` |
| Compatible new feature | `1.1.0` |
| Incompatible API change | `2.0.0` |

For a base-image fix without application logic changes, update the pinned digest
in `Dockerfile` **and bump `APP_VERSION`**, typically a patch. This project versions
the whole deliverable, including its dependencies. The pipeline validates the
chosen version; it cannot infer API compatibility. Patches have no artificial cap:
`1.0.3` can become `1.0.4`. Commit the version bump with the change.

The release job refuses to overwrite an existing version with different packaging,
and a new release must exceed the highest published version. ECR uses immutable
tags. Reruns and documentation-only commits reuse an image when both the source
version and its packaging revision match. The separate rollback workflow can
explicitly deploy an older published version without changing the source version.

## Container choices

The Dockerfile uses an official slim Python image pinned by digest, a hash-verified
Gunicorn dependency, a non-root user, root-owned application files, a health check,
and graceful termination. Only explicitly allowed files enter the build context.
Dependency installation is cached separately from source and image version changes.
There are no compiler or OS package installation steps and no extra build stage
because this application needs only a pure Python wheel.

## AWS pipeline

All work happens on `main`. `.github/workflows/ship.yml` has two jobs:

1. **Terraform (manual opt-in):** Plans and applies `infra/` when
   **Apply AWS infrastructure with Terraform** is checked (default: off).
2. **Image upload and deployment (manual):** Tests Python, builds a container,
   tests that exact container, uploads it to private ECR, deploys it to Lightsail,
   and verifies the live version when **Build, upload to ECR, and deploy the image
   to Lightsail** is checked (default: on). If the packaging commit was already
   published, it reuses that image and skips rebuilding/uploading it.

Open **GitHub Actions -> Ship to Lightsail -> Run workflow**, select `main`, and
choose which jobs to run. Select both for the first infrastructure/application
deployment, only Terraform for infrastructure changes, or only image release for
application/base-image changes. If selected Terraform fails, image release stops.
Pushing a commit does not run either job.

**Interview requirement:** Stage 2 of the brief requires every code change to reach
AWS without a manual step,
so manual-only releases do not yet satisfy that requirement. To meet it later, add
the `push` trigger for `main` and allow the image job to run on pushes as well as
when its manual checkbox is selected. Terraform can remain manual.

Infrastructure, release, and rollback workflows share the `production-lightsail`
concurrency group, so a running deployment is not cancelled or overlapped by
another operation. GitHub retains at most one pending run in this group; a newer
request can replace a pending request. Inspect the Actions queue when scheduling
multiple operations.

Release identity is checked using Git history for `Dockerfile`, `.dockerignore`,
`requirements.txt`, `hello_world.py`, `.github/workflows/ship.yml`,
`scripts/release.py`, and `scripts/check_service.py`. If the Dockerfile starts
copying additional source files, add them to `PACKAGING_PATHS` in
`scripts/release.py`. ECR stores an immutable `packaging-<commit SHA>` alias on
the same image as the SemVer tag. This identifies reruns without creating another
version; the Git commit is also recorded in the OCI revision label. The digest is
saved in deployment metadata. Keep both ECR tags for images needed for rollback.

### One-time setup

The pipeline needs an AWS identity and durable Terraform state before it can run.
`infra/bootstrap/` creates those prerequisites using your local exam credentials.
It creates an encrypted, private, versioned S3 bucket, a GitHub OIDC provider, and
the bounded `exam-github-terraform` role. No long-lived AWS credentials go into GitHub.

1. Use Terraform **1.13.5** (or compatible 1.10+) and the **exam account's** AWS
   credentials. Check `aws sts get-caller-identity`; verify the account contains
   `candidate-exam-boundary`. The provider rejects an unexpected account ID.
2. The operator needs the permissions in `infra/bootstrap/operator-policy.json`;
   replace `<ACCOUNT_ID>` before asking the account administrator to grant them.
   The exam boundary must also allow the state, ECR, Lightsail, and IAM actions
   used by the two workflow roles. These policies cannot override a boundary deny.
3. Run the bootstrap once, replacing the account ID below:

   ```sh
   export TF_VAR_aws_account_id="YOUR_12_DIGIT_EXAM_ACCOUNT_ID"
   terraform -chdir=infra/bootstrap init
   terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
   terraform -chdir=infra/bootstrap apply bootstrap.tfplan
   terraform -chdir=infra/bootstrap output github_repository_variables
   ```

   If GitHub's OIDC provider already exists in the account, set
   `TF_VAR_create_github_oidc_provider=false` before planning to reuse it.
   Bootstrap state stays locally in the ignored `infra/bootstrap/terraform.tfstate`;
   keep a private backup and reuse it for later bootstrap changes. The application
   infrastructure state is stored in S3 with a lock file.
4. Under **GitHub Settings -> Secrets and variables -> Actions -> Variables**, add
   the bootstrap outputs `AWS_ACCOUNT_ID` and `TF_STATE_BUCKET`. They are identifiers,
   not secrets. The role names and London region are configured in the workflow.
5. Commit/push the workflow to `main`, then choose **Run workflow** with both
   checkboxes selected for the first deployment. The workflow must be on the
   default branch before its manual button is available. A push only saves the
   files; the manual run provisions infrastructure and releases the image.

### What Terraform owns

- `infra/bootstrap/`: S3 state storage, GitHub OIDC, and `exam-github-terraform`.
- `infra/`: private ECR with immutable tags and scan-on-push, a one-node Nano
  Lightsail service, private-ECR pull policy, and `exam-github-deploy`.
- Both roles start with `exam-` and use `candidate-exam-boundary`.
- Lightsail manages its own ECR puller principal. Terraform grants that returned
  principal repository access; it does not create a custom replacement role.
- CI owns the image deployments, port `8080`, and `/healthz` public health check.
  Terraform owns capacity; subsequent infrastructure applies do not redeploy an
  old image. TLS is provided by the default Lightsail HTTPS endpoint.

Terraform protects the state bucket, ECR repository, and Lightsail service from
accidental destruction. Changing `lightsail_power` or `lightsail_scale` in
`infra/variables.tf` and manually running Terraform adjusts capacity.

### Verification and rollback (Stage 4)

The release workflow waits for the **requested Lightsail deployment number** to
become `ACTIVE`, confirms the image reference, and checks `/healthz` and the shared
`version` through HTTPS. A healthy old deployment does not count as a successful
release. Failed unit tests, builds, or container smoke tests stop publication and
deployment. The run summary records the URL, app/image version, and ECR digest.

To restore a specific previously published image:

1. Open **GitHub Actions -> Roll back Lightsail -> Run workflow**.
2. Select **main**, enter an existing ECR SemVer in **version**, such as `1.0.0`,
   and run the workflow. Find retained versions under **ECR -> mentee-test -> Images**
   or in previous release run summaries. Select the last known-good version to
   return to the previous release.
3. The workflow validates the input, requires an immutable ECR repository, resolves
   the tag and digest, pulls that exact image, and smoke-tests its reported version.
   Missing tags, invalid versions, or failed smoke tests stop before deployment.
4. It saves the current deployment, changes the app image in its existing container
   configuration, and creates a Lightsail deployment. It keeps the current ports,
   environment, other containers, and public endpoint settings. It waits for the
   requested deployment to become active and verifies the selected version over HTTPS.

ECR stores images; [Lightsail deploys the selected ECR image](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-container-service-ecr-private-repo-access.html).
Rollback never builds, pushes, overwrites a tag, or changes `APP_VERSION` on `main`.
Verification uses the **selected rollback version**, independently of current source.
Both workflows use the existing `exam-github-deploy` OIDC role and repository
variable `AWS_ACCOUNT_ID`; rollback does not need Terraform state access.

Each deployment run saves `release-metadata.json` (selected version, URI, digest,
and operation) and, when present, `previous-deployment.json` as artifacts for 14 days.
Images remain in ECR without an expiry policy, so a version rollback is independent
of artifact retention. There must already be an active service deployment to roll
back. This is an image rollback: it does not restore old infrastructure/configuration
or reverse database migrations. This service is stateless. Automatic rollback after
a failed release is a future improvement; the manual workflow reports failures.

To run the same rollback helper from a configured operator shell:

```sh
python3 scripts/release.py rollback --version 1.0.0
```

The workflow adds a container smoke test before calling this helper. No AWS
resources are needed to review these files or run the mocked tests below.

### Local checks

```sh
python3 -m unittest discover -s tests -v
terraform fmt -check -recursive infra
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
terraform -chdir=infra test
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap validate
terraform -chdir=infra/bootstrap test
```

Terraform tests use mocked AWS providers and do not create resources. An actual
Terraform plan/apply, Docker build, and AWS deployment require the configured
tools and account access; they are separate from these local checks.
