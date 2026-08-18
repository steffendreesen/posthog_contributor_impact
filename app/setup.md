# Dashboard setup

Static D3 dashboard served by nginx. The dataset in `public/data/` is copied into the image at build time.

Run all commands from the `app/` directory unless noted.

## Local

```bash
make build
make run
```

Open [http://localhost:8080](http://localhost:8080). Stop with Ctrl+C, or `make stop` from another terminal.

Without Docker:

```bash
make dev
```

## Environment variables

Set these before any GCP command:

```bash
export GCP_PROJECT_ID=sd-weave-interview
export REGION=us-central1
export REPO=posthog-contributor-impact
export IMAGE=dashboard
export SERVICE=posthog-contributor-impact
```

Derived image URL:

```bash
export IMAGE_URI="${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${REPO}/${IMAGE}:latest"
```

## Create the GCP project (one-time)

Skip if `${GCP_PROJECT_ID}` already exists. Look up a billing account with `gcloud billing accounts list`, then:

```bash
gcloud projects create "${GCP_PROJECT_ID}" --name="${GCP_PROJECT_ID}"

gcloud billing projects link "${GCP_PROJECT_ID}" \
  --billing-account="${BILLING_ACCOUNT_ID}"

gcloud config set project "${GCP_PROJECT_ID}"
```

## One-time GCP setup

```bash
gcloud config set project "${GCP_PROJECT_ID}"

gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  --project="${GCP_PROJECT_ID}"

gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --description="PostHog contributor impact dashboard"

PROJECT_NUMBER="$(gcloud projects describe "${GCP_PROJECT_ID}" --format='value(projectNumber)')"

gcloud artifacts repositories add-iam-policy-binding "${REPO}" \
  --location="${REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud artifacts repositories add-iam-policy-binding "${REPO}" \
  --location="${REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
```

Skip repository creation if `${REPO}` already exists in `${REGION}`.

## Submit a build to Artifact Registry

From `app/`, using Cloud Build:

```bash
gcloud builds submit \
  --tag "${IMAGE_URI}" \
  --project="${GCP_PROJECT_ID}"
```

Or build and push locally:

```bash
docker build -t "${IMAGE_URI}" .
docker push "${IMAGE_URI}"
```

## Create a public Cloud Run service

First deploy creates the service and makes it publicly reachable (`--allow-unauthenticated`):

```bash
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --project "${GCP_PROJECT_ID}" \
  --allow-unauthenticated \
  --port 8080
```

Confirm public access:

```bash
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --region="${REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --member="allUsers" \
  --role="roles/run.invoker"
```

Print the URL:

```bash
gcloud run services describe "${SERVICE}" \
  --region="${REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --format='value(status.url)'
```

## Deploy an update

Rebuild, push, and roll out with the same deploy command:

```bash
gcloud builds submit \
  --tag "${IMAGE_URI}" \
  --project="${GCP_PROJECT_ID}"

gcloud run deploy "${SERVICE}" \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --allow-unauthenticated \
  --port 8080
```
