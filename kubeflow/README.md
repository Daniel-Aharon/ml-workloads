# Kubeflow on EKS

Kubeflow Pipelines Standalone + JupyterHub deployed on AWS EKS via GitHub Actions CI/CD.

---

## What This Is

- **Kubeflow Pipelines Standalone:** ML workflow orchestration (DAG execution, artifact storage, UI)
- **JupyterHub:** Multi-user notebook environment pre-installed with KFP SDK
- **Infrastructure as Code:** Terraform (EKS cluster) + Kustomize (KFP manifests) + Helm (JupyterHub)
- **GitOps:** Push to `main` → GitHub Actions → Automatic deployment to EKS

## Architecture

```
GitHub (ml-workloads repo)
  ↓
GitHub Actions (OIDC authentication)
  ↓
AWS IAM (github-actions-role assumes temporary credentials)
  ↓
EKS Cluster (kubeflow-cluster, 2x t3.xlarge nodes)
  ├─ kubeflow namespace
  │  ├─ ml-pipeline (API server)
  │  ├─ ml-pipeline-ui (UI on port 8080)
  │  ├─ mysql (KFP metadata store)
  │  └─ minio (artifact storage)
  │
  └─ jupyterhub namespace
     ├─ hub (control plane)
     ├─ proxy (HTTP router, LoadBalancer NLB)
     └─ singleuser pods (user notebooks, on-demand)
```

**Key Components:**
- **KFP API & UI:** Orchestrates pipeline runs, displays DAGs, stores metadata in MySQL
- **MinIO:** S3-compatible object storage for pipeline artifacts
- **JupyterHub:** Spawns notebook pods per user, persistent home dir (PVC)
- **EBS StorageClass (gp3):** Persistent volumes for MySQL, MinIO, notebook homes

## Prerequisites

- **AWS account** with `kubeflow-cluster` EKS cluster running
- **AWS CLI** v2.13+
- **kubectl** v1.27+ (matches EKS cluster version)
- **kustomize** v5.4.1+
- **helm** v3.10+
- **git** (for cloning this repo)

### GitHub Configuration

Set these repository variables in `Settings → Secrets and variables → Variables`:
```
AWS_REGION          us-east-1 (or your region)
AWS_ACCOUNT_ID      123456789012
KUBEFLOW_CLUSTER_NAME kubeflow-cluster
```

The `github-actions-role` IAM role must exist in your AWS account with:
- Trust policy: GitHub OIDC provider
- Permissions: `AdministratorAccess` (for EKS API calls)
- EKS Access Entry: cluster-admin on the kubeflow-cluster

## Deployment

### Option 1: Automatic (Recommended)

```bash
# 1. Ensure cluster exists
cd ../../../eks-terraform/kubeflow-cluster
terraform apply

# 2. Push to main
cd ../../../ml-workloads
git add -A
git commit -m "deploy kubeflow"
git push origin main

# 3. Watch GitHub Actions
# Actions → deploy-kubeflow-pipelines workflow → check logs
```

The workflow will:
1. Validate AWS credentials (OIDC)
2. Deploy Kubeflow manifests (kustomize build → kubectl apply)
3. Wait for MySQL, MinIO, ML Pipeline API, ML Pipeline UI to be ready (5m timeout each)
4. Deploy JupyterHub via Helm (version 3.3.7)
5. Wait for JupyterHub hub to be ready

**Estimated time:** 8-12 minutes

### Option 2: Manual (for testing)

```bash
# 1. Update kubeconfig
aws eks update-kubeconfig --name kubeflow-cluster --region us-east-1

# 2. Deploy Kubeflow
kustomize build manifests/overlays/dev | kubectl apply -f -

# 3. Wait for rollout
kubectl rollout status deployment/ml-pipeline-ui -n kubeflow --timeout=5m

# 4. Deploy JupyterHub
helm repo add jupyterhub https://hub.jupyter.org/helm-chart/
helm upgrade --install jupyterhub jupyterhub/jupyterhub \
  --namespace jupyterhub --create-namespace \
  --version 3.3.7 \
  --values helm/jupyterhub-values.yaml
```

## Usage

### Access KFP UI

```bash
kubectl port-forward svc/ml-pipeline-ui 8080:80 -n kubeflow
# Open http://localhost:8080
```

### Access JupyterHub

```bash
kubectl port-forward svc/proxy-public 8081:80 -n jupyterhub
# Open http://localhost:8081
# Login: username=any, password=test (dummy auth)
```

### Submit Demo Pipeline

From a JupyterHub notebook:

```python
from kubeflow.pipelines import client
from pipelines.demo_pipeline import demo_pipeline

# Create KFP client
kfp_client = client.Client(host='http://ml-pipeline-ui.kubeflow:80')

# Submit pipeline
run = kfp_client.create_run_from_pipeline_func(demo_pipeline, arguments={})
print(f'Pipeline run: {run.run_id}')
```

Then view the DAG execution in KFP UI:
- Refresh `http://localhost:8080`
- Expand "Experiments" → "demo-pipeline"
- Click the run to see the DAG

## Pipeline Architecture

The demo pipeline is intentionally minimal (2 components):

```
generate_data() [outputs CSV artifact to MinIO]
      ↓
process_data() [reads CSV, returns mean value]
```

**Dependencies:**
- **MinIO:** Artifacts are uploaded to MinIO (S3 compatible)
- **MySQL:** Pipeline metadata (run history, logs) stored in MySQL (Lite in this case)
- **KFP API:** Orchestrates component execution as Kubernetes pod steps
- **Argo Workflow:** KFP compiles to Argo Workflow YAML (executed by Kubernetes)

**Component Details:**
- `generate_data()`: Creates `/tmp/data.csv` (100 rows), uploads to MinIO via KFP Output artifact
- `process_data()`: Reads CSV from MinIO, computes mean, returns as metric

Both run in isolated containers (`python:3.11-slim`) with auto-installed dependencies.

## File Structure

```
kubeflow/
├── manifests/
│   ├── base/
│   │   ├── kustomization.yaml       
│   │   └── storageclass-gp3.yaml     
│   └── overlays/dev/
│       ├── kustomization.yaml        
│       └── patches/
│           ├── mysql-resources.yaml 
│           └── minio-resources.yaml  
├── helm/
│   └── jupyterhub-values.yaml       
├── pipelines/
│   └── demo-pipeline.py          
├── .github/workflows/
│   └── deploy-kubeflow-pipelines.yaml 
└── README.md                         
```

## Resource Constraints

**Cluster:** 2x t3.xlarge nodes (4 vCPU, 16GB RAM each) = 32GB total

**Allocation:**
- **Kubeflow:** 4-6GB (MySQL, MinIO, KFP API, KFP UI)
- **JupyterHub:** ~2GB (hub, proxy, persistent state)
- **User notebooks:** 500m CPU, 1Gi memory per user (supports ~7-8 concurrent users)
- **OS/System reserved:** ~2GB per node

**Key Constraints:**
- No memory overcommit (pods get OOMKilled)
- CPU requests honored, limits allow bursting
- PVC provisioning is AZ-aware (WaitForFirstConsumer)

## Troubleshooting

### PVC Pending

**Symptom:** `kubectl get pvc -n kubeflow` shows STATUS=Pending

**Cause:** EBS CSI driver not running or StorageClass misconfigured

**Fix:**
```bash
# Check EBS CSI driver
kubectl get pods -n kube-system | grep ebs

# Verify StorageClass
kubectl get storageclass gp3-ebs
kubectl describe storageclass gp3-ebs
```

### Pod OOMKill

**Symptom:** Pod restarts repeatedly, events show `OOMKilled`

**Cause:** Memory request/limit too low for workload

**Fix:**
- Increase memory limit in `helm/jupyterhub-values.yaml` (singleuser section)
- Redeploy: `helm upgrade jupyterhub jupyterhub/jupyterhub ...`

### Pipeline Submission Fails

**Symptom:** `kfp_client.create_run_from_pipeline_func()` returns error

**Causes & fixes:**
- **MinIO unreachable:** `kubectl get pods -n kubeflow | grep minio` must be Running
- **KFP API unreachable:** Change host to `http://ml-pipeline.kubeflow:8888` (internal DNS)
- **Artifact upload fails:** Check MinIO credentials in KFP UI → Settings

### OIDC Auth Fails in GitHub Actions

**Symptom:** Workflow step "Configure AWS credentials" fails with "invalid token"

**Causes & fixes:**
- **Repo not in OIDC trust policy:** Check `cloud-infra/oidc.tf` includes your repo
- **Wrong AWS_ACCOUNT_ID:** Verify GitHub variable matches your AWS account
- **Role doesn't exist:** Check `aws iam get-role --role-name github-actions-role`

## Interview Talking Points

1. **Resource-Conscious Architecture:** "Full Kubeflow needs 40-60GB; I chose Standalone to fit in 32GB and show architectural thinking."

2. **OIDC Security:** "GitHub Actions uses OIDC (not long-lived keys). Tokens expire in 15 minutes and are auditable in CloudTrail per repository."

3. **GitOps:** "Push to main triggers automatic deployment. Manifests are source of truth. Reproducible, auditable, version-controlled."

4. **Kustomize Pattern:** "I reference upstream KFP 2.3.0 without forking. Overlays patch resources for different environments. Maintainable."

5. **Storage Architecture:** "StorageClass with WaitForFirstConsumer ensures pods and volumes are in the same AZ (EBS mount requirement)."

---
