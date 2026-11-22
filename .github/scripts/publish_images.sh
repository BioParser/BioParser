#!/usr/bin/env bash
# Publish every application image, or leave the registry unchanged.
# Each image is built before any push. Floating tags move only after every sha
# tag push succeeds. api:latest moves last, because staging imports that tag
# on its own schedule.

set -euo pipefail

prefix=ghcr.io/bioparser/bioparser
: "${SHA:?}"

specs=$(cat <<EOF
parser-worker|docker/parser-worker.Dockerfile||${SHA}|latest
extractor-worker|docker/extractor-worker.Dockerfile||${SHA}|latest
migrate|docker/migrate.Dockerfile||${SHA}|latest
mineru|docker/mineru.Dockerfile|cpu|${SHA}-cpu|cpu
api|docker/api.Dockerfile||${SHA}|latest
EOF
)

while IFS='|' read -r name file target sha_tag float_tag; do
  [[ -z "${name}" ]] && continue
  args=(buildx build --load -f "${file}" -t "${prefix}/${name}:${sha_tag}")
  if [[ -n "${target}" ]]; then
    args+=(--target "${target}")
  fi
  docker "${args[@]}" .
done <<< "${specs}"

while IFS='|' read -r name file target sha_tag float_tag; do
  [[ -z "${name}" ]] && continue
  docker push "${prefix}/${name}:${sha_tag}"
done <<< "${specs}"

while IFS='|' read -r name file target sha_tag float_tag; do
  [[ -z "${name}" ]] && continue
  docker tag "${prefix}/${name}:${sha_tag}" "${prefix}/${name}:${float_tag}"
  docker push "${prefix}/${name}:${float_tag}"
done <<< "${specs}"
