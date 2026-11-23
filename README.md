# mentee-test

A small Python service that reports independent app and container image versions.

Print the version locally (Python 3.13, no dependencies required):

```sh
python3 hello_world.py
```

Without a built image, `image_version` is `local`. The app version is `1.0.0`.

Build and run the container:

```sh
docker build --build-arg IMAGE_VERSION=1.0.0 -t mentee-test:1.0.0 .
docker run --rm --name mentee-test -p 8080:8080 --read-only --cap-drop=ALL --security-opt=no-new-privileges mentee-test:1.0.0
```

In another terminal, inspect the live version and health:

```sh
curl http://localhost:8080/version
curl http://localhost:8080/healthz
```

`/` and `/version` return:

```json
{"message": "Hello, world!", "app_version": "1.0.0", "image_version": "1.0.0"}
```

`/healthz` returns `{"status": "ok"}`. The service listens on port 8080 and
writes access and error logs to stdout/stderr. For local HTTP development:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/gunicorn --bind 127.0.0.1:8080 hello_world:app
```

## Versioning

- **App version:** `APP_VERSION` in `hello_world.py`, initially `1.0.0`.
- **Image version:** an independent `Major.Minor.Patch` value passed as
  `IMAGE_VERSION` during the Docker build. It is baked into `/app/IMAGE_VERSION`
  and the OCI image version label. Python reads the baked file.
- A container cannot automatically discover the registry tag it was pulled with.
  The release pipeline must use the same value for the build argument and image tag,
  and publish immutable tags. Adding another tag does not change the baked version.
- For example, a base image security fix can keep app `1.0.0` and produce image
  `1.0.1`. Update the pinned base digest, rebuild with `IMAGE_VERSION=1.0.1`,
  and publish the image as `mentee-test:1.0.1`.

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
   to Lightsail** is checked (default: on).

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

Infrastructure and releases share a concurrency group and running deployments are
not cancelled by a later workflow run. Version selection happens automatically
inside each manually started image release; there is no version field to type in.

- **App:** `APP_VERSION` in Python describes the API: patch for compatible fixes,
  minor for compatible features, major for incompatible changes. Update it in the
  source when the app changes; the pipeline cannot infer compatibility from code.
- **Image:** Start at the app version when ECR has no release tags. Otherwise take
  the greater of the app version and the highest existing image SemVer plus one
  patch. This gives every new publication a unique version, including rebuilds.
- Use exactly `Major.Minor.Patch` for this exercise. ECR prevents overwriting release
  tags. The Docker build argument, OCI version label, pushed tag, and Python's
  `image_version` all use the same value. The Git commit is recorded separately in
  the OCI revision label and the image digest in release metadata.

For example, in release order:

| Change | App version | Image version |
| --- | --- | --- |
| First release | `1.0.0` | `1.0.0` |
| Base-image security fix | `1.0.0` | `1.0.1` |
| Compatible application bug fix | `1.0.1` | `1.0.2` |
| Compatible feature | `1.1.0` | `1.1.0` |
| Breaking API change | `2.0.0` | `2.0.0` |

This uses [SemVer compatibility rules](https://semver.org/) for the app and an
explicit packaging-rebuild policy for image patches. Rollbacks reuse an existing
image; starting a new build is a new publication, not a rollback.

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

### Verification and rollback

The workflow waits for the requested Lightsail deployment version to become
`ACTIVE`, checks that it uses the expected image, and verifies both versions
through HTTPS. A healthy old deployment is not accepted as a successful release.
The run summary shows the service URL, app version, image version, and image digest.

The workflow saves `release-metadata.json` and, when available,
`previous-deployment.json` as run artifacts for 14 days. For a manual rollback,
download the previous configuration and run:

```sh
aws lightsail create-container-service-deployment \
  --region eu-west-2 --cli-input-json file://previous-deployment.json
```

Wait for it to become active and inspect `/version`. The ECR images are retained;
rollback reuses an existing image. Automatic rollback is a future improvement.

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
