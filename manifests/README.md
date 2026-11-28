# OKD/Kubernetes manifests

This folder includes the OKD/Kubernetes manifests (configs) for running BioParser on an OKD cluster. Docker compose can, and should be used instead, when running BioParser locally. See the project's main [README](../README.md) for more information about running BioParser using Docker Compose.

## Configs and secrets

### Configs (ConfigMap)

Copy [bioparser-configmap-example.yaml](bioparser-configmap-example.yaml) to `bioparser-configmap.yaml` in this folder. Git ignores the copy. Set `namespace` in the copy, then run:

```sh
oc apply -f manifests/bioparser-configmap.yaml
```

The ConfigMap holds no secret. `BIOPARSER_REDIS_URL` and `BIOPARSER_DATABASE_URL` hold no password: the API reads the passwords from files.

### Secrets (Secret)

The pods read their secrets from files in `/run/secrets`. No secret is in an environment variable, a command line, the ConfigMap or Git. All secrets are in the Secret `bioparser-secret`. Each pod mounts only its own keys:

| Pod | Key in `bioparser-secret` | File in `/run/secrets` |
|---|---|---|
| `bioparser-api-dep` | `REDIS_PASSWORD` | `bioparser_redis_password` |
| `bioparser-api-dep` | `POSTGRES_APP_PASSWORD` | `bioparser_database_password` |
| `bioparser-api-dep` | `BIOPARSER_API_KEY` | `bioparser_api_key` |
| `redis` | `REDIS_PASSWORD` | `redis_password` |
| `vllm-server` | `HF_TOKEN` | `hf_token` |

`POSTGRES_PASSWORD` is for Postgres and `migrate`. No manifest in this folder uses it yet.

Each file has mode `0440`: only the owner (root) and the group of the file can read it. On OKD, the container user is in that group. The pods do not use the Kubernetes API. They do not mount the token of that API.

Do these steps in the root directory of the repository. The script writes to the directory in `BIOPARSER_SECRETS_DIR` (from the shell, else from `.env`), else to `./secrets`. The local Compose stack reads the same directory. For the cluster, use a different directory outside the checkout. Then the cluster does not use the passwords of your local stack, and Git and the image build do not see the files.

The commands below give `BIOPARSER_SECRETS_DIR` only to the script, and they do not export it. For this reason, the local Compose stack keeps its own directory. In a new shell, set `OKD_SECRETS_DIR` again.

1. Set the directory of the cluster files. Then make a random value for each password and for the API key.
   ```sh
   OKD_SECRETS_DIR="$HOME/bioparser-okd-secrets"
   BIOPARSER_SECRETS_DIR="$OKD_SECRETS_DIR" python3 src/bioparser/scripts/generate_secrets.py
   ```
2. Write your Hugging Face **read** token to `$OKD_SECRETS_DIR/hf_token`. If the file is empty, vLLM uses no token.
3. Make the Secret. The command line holds the file names, not the values.
   ```sh
   oc create secret generic bioparser-secret \
     --from-file=REDIS_PASSWORD="$OKD_SECRETS_DIR/redis_password" \
     --from-file=POSTGRES_PASSWORD="$OKD_SECRETS_DIR/postgres_password" \
     --from-file=POSTGRES_APP_PASSWORD="$OKD_SECRETS_DIR/postgres_app_password" \
     --from-file=BIOPARSER_API_KEY="$OKD_SECRETS_DIR/bioparser_api_key" \
     --from-file=HF_TOKEN="$OKD_SECRETS_DIR/hf_token"
   ```

The script writes 64 hex digits. Your own Redis password must be printable ASCII without spaces, double quotes or backslashes. If it has other characters, Redis does not start, and the log of the pod shows the cause.

**Rotate a secret.** Kubernetes changes the files in a pod after some time. Redis and the API read the files only at start. Restart the pods after the change:

```sh
OKD_SECRETS_DIR="$HOME/bioparser-okd-secrets"
rm "$OKD_SECRETS_DIR/redis_password"
BIOPARSER_SECRETS_DIR="$OKD_SECRETS_DIR" python3 src/bioparser/scripts/generate_secrets.py
oc set data secret/bioparser-secret --from-file=REDIS_PASSWORD="$OKD_SECRETS_DIR/redis_password"
oc rollout restart deployment/redis deployment/bioparser-api-dep
```

For a different key, change the key and the file name, and restart only the pods of that key in the table. For a Postgres password, set the new password in Postgres first. See "Secrets" in [docs/docker-compose.md](../docs/docker-compose.md).

**A cluster with the old manifests.** The old manifests gave all secrets to the API as environment variables, and Redis had no password.

1. Give Redis a password. Make the files in the directory of the cluster, then add only the Redis key to the Secret. The Secret keeps its old values for the other keys.
   ```sh
   OKD_SECRETS_DIR="$HOME/bioparser-okd-secrets"
   BIOPARSER_SECRETS_DIR="$OKD_SECRETS_DIR" python3 src/bioparser/scripts/generate_secrets.py
   oc set data secret/bioparser-secret --from-file=REDIS_PASSWORD="$OKD_SECRETS_DIR/redis_password"
   ```
2. Remove the passwords from `BIOPARSER_REDIS_URL` and `BIOPARSER_DATABASE_URL` in your `bioparser-configmap.yaml`, then run `oc apply -f manifests/bioparser-configmap.yaml`. A password in a URL has priority over the file.
3. Update the three Deployments. Kubernetes starts new pods with the files.
   ```sh
   oc apply -f manifests/redis/redis-deployment.yaml -f manifests/api/api-deployment.yaml -f manifests/vllm/vllm-deployment.yaml
   ```
