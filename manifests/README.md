# OKD/Kubernetes manifests

This folder includes the OKD/Kubernetes manifests (configs) for running BioParser on an OKD cluster. Docker compose can, and should be used instead, when running BioParser locally. See the project's main [README](../README.md) for more information about running BioParser using Docker Compose.

## Configs and secrets

This folder includes files [bioparser-configmap-example.yaml](/manifests/bioparser-configmap-example.yaml) and [bioparser-secret-example.yaml](/manifests/bioparser-secret-example.yaml). These files should be copied and named bioparser-configmap.yaml and bioparser-secret.yaml. After this, the files should be filled with the required environment variables. See the following two sections for this.

### Configs (configmap)

[bioparser-configmap-example.yaml](/manifests/bioparser-configmap-example.yaml) includes the **non-secret** configurations. The following variables need to be set:
- namespace
- BIOPARSER_REDIS_URL - the URL of redis (redis://:${REDIS_PASSWORD}@redis:6379/0)
- BIOPARSER_DATABASE_URL - the URL of the used Postgres database (postgresql://bioparser_app:${POSTGRES_APP_PASSWORD}@postgres:5432/bioparser)

### Secrets (secret)

**NOTE:** All of the variables outside of the namespace, need to be base64-encoded values. A given value can be easily encoded using `echo -n "secret-value" | base64`. 

[bioparser-secret-example.yaml](/manifests/bioparser-secret-example.yaml) includes the **secrets**. The following variables need to be set:
- namespace
- POSTGRES_PASSWORD - generate using `openssl rand -hex 32`
- POSTGRES_APP_PASSWORD - generate using `openssl rand -hex 32`
- HF_TOKEN - your huggingface **read** token for downloading the required models
- BIOPARSER_API_KEY - generate with python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
