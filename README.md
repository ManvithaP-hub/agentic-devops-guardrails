# agentic-devops-guardrails

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A five-layer guardrail architecture for governing autonomous AI agents in cloud-native DevOps pipelines on AWS. This repository contains the reference implementation, evaluation framework, and empirical results.

---

## The Problem

Autonomous AI agents operating in DevOps pipelines can fail in unbounded ways — hallucinating infrastructure states, misinterpreting instruction scope, or being manipulated by adversarial inputs embedded in monitoring alerts or incident tickets. Existing safety mechanisms address these risks incompletely:

- **LLM output filters** intercept what the model says but not what the agent does
- **Policy-as-code frameworks** enforce rules at deployment time, not at agent runtime against live infrastructure state
- **Human approval workflows** operate on timescales incompatible with autonomous agents

This repository provides a unified five-layer guardrail pipeline that intercepts agent actions at the tool-call execution boundary before they affect infrastructure.

---

## Architecture

```
AI Agent Action Prompt
        │
        ▼
┌─────────────────────────────┐
│  Blast-Radius Scorer        │  B < 0.3  → auto-approve
│  B(a) ∈ [0, 1]             │  B ≥ 0.3  → Layer 1 + 2
└─────────────┬───────────────┘  B ≥ 0.9  → full pipeline + HITL
              ▼
┌─────────────────────────────┐
│  Layer 1: Bedrock Guardrail │  LLM output filter
│  Topic deny + PII block     │  ~400ms when blocked
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│  Layer 2: OPA Live State    │  Direct boto3 SDK calls
│  ec2 + s3 + iam per-call   │  No cache. No CloudTrail.
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│  Layer 3: Confidence Delta  │  Claude Haiku 4.5 A/B
│  Hedging vocab frequency    │  Threshold: δ ≥ 0.55
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│  Layer 4: HITL Gate         │  DynamoDB TTL = 300s
│  Silence equals rejection   │  SNS email + API Gateway
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│  Layer 5: Audit + Rollback  │  CloudWatch structured JSON
│  S3 snapshot before action  │  Auto-rollback if rate < 70%
└─────────────────────────────┘
```

### Blast-Radius Scoring

Every proposed agent action receives a normalized impact score B(a) ∈ [0, 1]:

| Risk Tier | Score | Vocabulary |
|---|---|---|
| Low-risk | 0.1 | list, get, describe, status, monitor, read, query |
| Medium-risk | 0.4 | restart, scale, update, patch, deploy, modify |
| High-risk | 0.9 | delete, destroy, terminate, drop, purge, wipe, erase |

### Layer 2: OPA Against Live Infrastructure State

Unlike admission-time policy engines that evaluate static manifests, Layer 2 pulls live AWS resource state via boto3 immediately before each policy evaluation — EC2 instance inventory with environment tags, S3 bucket list, and IAM role inventory. This live context enables the policy engine to distinguish staging from production operations at runtime, addressing the fundamental limitation of static policy evaluation.

### Layer 4: Silence Equals Rejection

The HITL gate is the most critical safety property of this architecture. When a high-risk action requires human approval, a DynamoDB record is written with TTL = now + 300 seconds. If no approver responds within 5 minutes, the record expires automatically and the action is denied. An unavailable approver never produces an implicit approval. This is enforced by native DynamoDB TTL with zero polling overhead.

---

## Empirical Results

The architecture was evaluated on live AWS infrastructure (us-east-1) across 100 prompts spanning five risk categories including 20 adversarial jailbreak variants, using Claude Haiku 4.5 as the inference model.

### Five-Layer Pipeline Results

| Category | N | Accuracy | FP Rate | FN Rate | Avg Latency |
|---|---|---|---|---|---|
| Read operations | 20 | 100% | 0% | 0% | 0ms |
| Safe staging changes | 20 | 70% | 30% | 0% | 12,638ms |
| Risky prod changes | 20 | **100%** | 0% | **0%** | 5,600ms |
| Destructive operations | 20 | **100%** | 0% | **0%** | 430ms |
| Adversarial jailbreaks | 20 | **100%** | 0% | **0%** | 897ms |
| **Total** | **100** | **94%** | **6%** | **0%** | **7,851ms** |

### Key Findings

**0% false negative rate across all 60 block-expected prompts.** Every destructive operation, risky production mutation, and adversarial jailbreak was blocked. This includes all 20 destructive operations (delete/destroy/terminate/purge) and all 20 adversarial variants including prompt injection, authority spoofing, role-playing attacks, and obfuscated intent.

**7.6x latency reduction for blocked actions.** Intercepted actions resolved at 1,828ms average versus 13,874ms for allowed actions. Layer 1 short-circuits the full LLM invocation for high-confidence blocks.

**Layer contribution breakdown:**
- Layer 1 (Bedrock Guardrail): blocked 48 prompts (80% of all blocks)
- Layer 2 (OPA live state): blocked 18 additional prompts (20% of all blocks)
- Layers 3-5: 0 additional blocks in this evaluation — demonstrating Layer 1 and 2 handle the primary enforcement load while Layers 3-5 provide defense-in-depth for edge cases

**The 6% false positive rate** (6 staging operations incorrectly blocked) represents cases where staging operations use vocabulary that overlaps with the DestructiveOps topic definition without explicit staging context keywords. These are addressed in production by the low-risk bypass mechanism for confirmed non-production environments.

---

## Why Single-Layer Guardrails Are Insufficient

A single Bedrock Guardrail layer evaluated against the same 100 prompts achieved:

| Metric | Layer 1 Only | Five Layers |
|---|---|---|
| Overall accuracy | 76% | **94%** |
| FN rate | 18% | **0%** |
| FN on risky prod | 16/20 | **0/20** |
| FN on destructive | 0/20 | **0/20** |

The 18 false negatives in the single-layer baseline consist entirely of medium-risk production mutations — actions using vocabulary like "restart", "update", "scale", "patch" targeting production resources. A vocabulary-based guardrail cannot distinguish these from equivalent staging operations because the production/staging distinction is encoded in live resource metadata, not in the action vocabulary itself. Layer 2 OPA with live boto3 state retrieval addresses this gap directly, reducing the FN rate from 18% to 0%.

This establishes empirically that multi-layer defense-in-depth is necessary, not merely sufficient: each layer addresses a failure mode the previous layer architecturally cannot resolve.

---

## Repository Structure

```
agentic-devops-guardrails/
├── layers/
│   ├── 1-bedrock/          # Layer 1: Bedrock Guardrail
│   ├── 2-opa/              # Layer 2: OPA + live boto3 state
│   ├── 3-confidence/       # Layer 3: Confidence delta scorer
│   ├── 4-hitl/             # Layer 4: DynamoDB TTL HITL gate
│   └── 5-audit/            # Layer 5: CloudWatch + S3 rollback
├── policies/
│   └── guardrail.rego      # OPA Rego policy rules
├── evaluate.py             # Layer 1 baseline evaluation (100 prompts)
├── evaluate_5layer.py      # Five-layer evaluation framework
├── results.txt             # Layer 1 baseline results
├── results_5layer.txt      # Five-layer verified results
└── terraform/              # Infrastructure as code
```

---

## Running the Evaluation

**Prerequisites:**
- AWS account with Amazon Bedrock access
- Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) enabled
- A Bedrock Guardrail configured with DestructiveOps and CredentialExposure denial topics

```bash
git clone https://github.com/ManvithaP-hub/agentic-devops-guardrails.git
cd agentic-devops-guardrails
python3 -m venv venv
source venv/bin/activate
pip install boto3

export BEDROCK_GUARDRAIL_ID=your-guardrail-id
export AWS_DEFAULT_REGION=us-east-1

# Layer 1 baseline evaluation
python3 evaluate.py

# Five-layer evaluation
python3 evaluate_5layer.py
```

Estimated runtime: 15-25 minutes for five-layer evaluation.
Estimated cost: under $0.10 USD.

---

## Terraform Deployment

The complete five-layer infrastructure can be deployed using the companion Terraform module:

```
https://github.com/ManvithaP-hub/terraform-aws-bedrock-guardrail
```

Also available on the Terraform Registry:

```
https://registry.terraform.io/modules/ManvithaP-hub/bedrock-guardrail/aws
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

---

## Author

**Manvitha Potluri**
DevOps Cloud Solutions Architect
[github.com/ManvithaP-hub](https://github.com/ManvithaP-hub)
