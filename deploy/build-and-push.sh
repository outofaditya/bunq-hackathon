#!/usr/bin/env bash
# Build the Mission Mode Docker image and push it to AWS ECR.
#
# Prereqs (one-time):
#   1. AWS CLI installed + `aws configure` done (or AWS_PROFILE exported)
#   2. Docker running locally
#   3. ECR repo will be auto-created on first run if it doesn't exist
#
# Usage:
#   ./deploy/build-and-push.sh                     # uses env defaults below
#   AWS_REGION=us-east-1 ./deploy/build-and-push.sh
#   IMAGE_TAG=v0.2 ./deploy/build-and-push.sh

set -euo pipefail

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
log()  { printf "${CYAN}→${RESET} %s\n" "$1"; }
ok()   { printf "${GREEN}✓${RESET} %s\n" "$1"; }
err()  { printf "${RED}✗${RESET} %s\n" "$1"; }

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# --- config (override via env) -------------------------------------------------
AWS_REGION="${AWS_REGION:-eu-central-1}"
ECR_REPO="${ECR_REPO:-mission-mode}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

# --- preflight -----------------------------------------------------------------
if ! command -v aws    >/dev/null 2>&1; then err "aws CLI not in PATH"; exit 1; fi
if ! command -v docker >/dev/null 2>&1; then err "docker not in PATH"; exit 1; fi

if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  log "discovering AWS account id…"
  AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi
if [[ -z "$AWS_ACCOUNT_ID" ]]; then
  err "AWS_ACCOUNT_ID could not be determined. Run 'aws configure' first."
  exit 1
fi

ECR_HOST="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${ECR_HOST}/${ECR_REPO}:${IMAGE_TAG}"
LATEST="${ECR_HOST}/${ECR_REPO}:latest"

log "image: ${IMAGE}"
log "       ${LATEST}"

# --- ensure repo exists --------------------------------------------------------
if ! aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1; then
  log "creating ECR repo $ECR_REPO in $AWS_REGION…"
  aws ecr create-repository \
    --repository-name "$ECR_REPO" \
    --region "$AWS_REGION" \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability MUTABLE \
    >/dev/null
  ok "ECR repo created"
fi

# --- login ---------------------------------------------------------------------
log "logging Docker into ECR ($AWS_REGION)…"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_HOST" >/dev/null
ok "logged in"

# --- build (App Runner runs linux/amd64) ---------------------------------------
log "building image (linux/amd64)…"
DOCKER_BUILDKIT=1 docker build \
  --platform linux/amd64 \
  --pull \
  -t "$IMAGE" \
  -t "$LATEST" \
  .
ok "built"

# --- push ---------------------------------------------------------------------
log "pushing $IMAGE …"
docker push "$IMAGE"
log "pushing $LATEST …"
docker push "$LATEST"

ok "done."
echo
printf "${YELLOW}Next:${RESET} point AWS App Runner at this image:\n"
printf "  ${GREEN}%s${RESET}\n\n" "$LATEST"
printf "Or update an existing service in one command:\n"
printf "  ${GREEN}aws apprunner start-deployment --service-arn <ARN> --region %s${RESET}\n" "$AWS_REGION"
