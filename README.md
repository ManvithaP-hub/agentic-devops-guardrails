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

Every proposed agent action is assigned one of three risk tiers by a vocabulary lookup on the action text, mapped to a score B(a) ∈ {0.1, 0.4, 0.9}:

| Risk Tier | Score | Vocabulary |
|---|---|---|
| Low-risk | 0.1 | list, get, describe, status, monitor, read, query |
| Medium-risk | 0.4 | restart, scale, update, patch, deploy, modify |
| High-risk | 0.9 | delete, destroy, terminate, drop, purge, wipe, erase |

### Layer 2: OPA Against Live Infrastructure State

Unlike admission-time policy engines that evaluate static manifests, Layer 2 pulls live AWS resource state via boto3 immediately before each policy evaluation — EC2 instance inventory with environment tags, S3 bucket list, and IAM role inventory. This live context enables the policy engine to distinguish staging from production operations at runtime, addressing the fundamental limitation of static policy evaluation.

> **Evaluation note:** The live-state implementation is in `layers/2-opa/`. The 100-prompt evaluation below (`evaluate_5layer.py`) used a rule-equivalent Python version of the same policies that reads environment indicators (e.g. `prod`, `staging`) from the action text, for reproducibility. The reported results therefore reflect rule-based Layer 2, not live boto3 lookups.

### Layer 4: Silence Equals Rejection

The HITL gate is the most critical safety property of this architecture. When a high-risk action requires human approval, a DynamoDB record is written with TTL = now + 300 seconds. If no approver responds within 5 minutes, the record expires automatically and the action is denied. An unavailable approver never produces an implicit approval. This is enforced by native DynamoDB TTL with zero polling overhead.

> **Evaluation note:** In the 100-prompt evaluation the HITL gate was simulated (`hitl_simulated: true` in `results_5layer.txt`): high-risk actions were treated as TTL-expired (rejected) and escalated medium-risk actions as approved. The DynamoDB/SNS implementation is in `layers/4-hitl/`.

---

## Empirical Results

The architecture was evaluated on live AWS infrastructure (us-east-1) across 100 prompts spanning five risk categories including 20 adversarial jailbreak variants, using Claude Haiku 4.5 as the inference model.

### Five-Layer Pipeline Results

All figures below are computed from `results_5layer.txt` (evaluation run 2026-07-02).

| Category | N | Expected | Blocked | Correct | Avg Latency |
|---|---|---|---|---|---|
| Read operations | 20 | allow | 0 | 20 | not timed* |
| Safe staging changes | 20 | allow | 6 | 14 | 10,103ms |
| Risky prod changes | 20 | block | 20 | **20** | 4,579ms |
| Destructive operations | 20 | block | 20 | **20** | 413ms |
| Adversarial jailbreaks | 20 | block | 20 | **20** | 649ms |
| **Total** | **100** | | **66** | **94** | **3,149ms** |

\* Read-only actions take the low-risk path and skip model invocation; latency is recorded as 0.

| Metric | Value |
|---|---|
| Overall accuracy | 94/100 (94%) |
| False negatives | 0/60 block-expected prompts (0%) |
| False positives | 6/40 allow-expected prompts (15%), i.e. 6% of all 100 prompts |
| Precision on blocks | 60/66 (90.9%) |
| Adversarial detection | 20/20 (100%) |

### Key Findings

**0 false negatives across all 60 block-expected prompts.** Every destructive operation, risky production mutation, and adversarial jailbreak was blocked, including all 20 adversarial variants (prompt injection, authority spoofing, role-playing, obfuscated intent).

**Blocked actions resolved 7.6x faster than allowed ones.** The 66 blocked actions averaged 1,828ms. The 14 allowed actions that required full model inference averaged 13,874ms. Content-filter blocks return before full inference completes (destructive 413ms, adversarial 649ms average), so the common attack path is also the cheapest.

**Layer contribution breakdown (66 blocks):**
- Layer 1 (Bedrock Guardrail): 48 blocks (73%): 20 destructive, 18 adversarial, 4 risky production, and the 6 staging false positives
- Layer 2 (OPA policy): 18 blocks (27%): 16 risky production changes and 2 adversarial prompts phrased as routine operations
- Layers 3-5: 0 additional blocks in this evaluation

**The 6 false positives** are staging changes (e.g. rotating staging API keys, scaling down staging read replicas) blocked by Layer 1 because their wording overlaps the destructive-operations topic definition. They occur identically with or without the other layers.

---

## Why Single-Layer Guardrails Are Insufficient

A single Bedrock Guardrail layer evaluated against the same 100 prompts achieved:

Results from `results.txt` (Layer 1 only) vs `results_5layer.txt` (five layers), same 100 prompts:

| Metric | Layer 1 Only | Five Layers |
|---|---|---|
| Overall accuracy | 76% | **94%** |
| False negatives (of 60) | 18 | **0** |
| FN on risky prod | 16/20 | **0/20** |
| FN on adversarial | 2/20 | **0/20** |
| FN on destructive | 0/20 | **0/20** |
| False positives (of 40) | 6 | 6 |

The 18 single-layer false negatives were 16 medium-risk production mutations (vocabulary like "restart", "update", "scale", "patch" aimed at production) and 2 adversarial prompts that framed a destructive outcome in routine operational language. A content filter cannot tell these apart from equivalent staging operations, because the production/staging distinction is about the target, not the verb. Adding the Layer 2 environment policy removed all 18 false negatives and added no new false positives.

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
