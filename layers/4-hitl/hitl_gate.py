import boto3, json, time, uuid, os
from datetime import datetime, timezone, timedelta

_REGION = os.environ.get("AWS_REGION", "us-east-1")
dynamodb = boto3.resource("dynamodb", region_name=_REGION)
ddb_client = boto3.client("dynamodb", region_name=_REGION)

TABLE_NAME  = os.environ.get("DYNAMODB_TABLE_NAME", "devops-agent-approvals")
TTL_MINUTES = 5

def ensure_table():
    existing = [t.name for t in dynamodb.tables.all()]
    if TABLE_NAME in existing:
        print(f"  Table {TABLE_NAME} already exists")
        return dynamodb.Table(TABLE_NAME)

    print(f"  Creating DynamoDB table {TABLE_NAME}...")
    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "approval_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "approval_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST"
    )
    table.wait_until_exists()

    # Enable TTL separately
    ddb_client.update_time_to_live(
        TableName=TABLE_NAME,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"}
    )
    print(f"  Table created with TTL enabled.")
    return table

def request_approval(prompt, risk, blast, layer1_blocked, layer2_decision):
    table       = ensure_table()
    approval_id = f"appr-{uuid.uuid4().hex[:8]}"
    expires_at  = datetime.now(timezone.utc) + timedelta(minutes=TTL_MINUTES)

    record = {
        "approval_id":     approval_id,
        "prompt":          prompt,
        "risk_level":      risk,
        "blast_radius":    str(blast),
        "layer1_blocked":  str(layer1_blocked),
        "layer2_decision": layer2_decision,
        "status":          "PENDING",
        "requested_at":    datetime.now(timezone.utc).isoformat(),
        "expires_at":      expires_at.isoformat(),
        "ttl":             int(expires_at.timestamp())
    }
    table.put_item(Item=record)
    return record

def hitl_gate(prompt, risk, blast, layer1_blocked, layer2_decision):
    start = time.time()

    if risk == "low" and not layer1_blocked and layer2_decision == "ALLOW":
        return {
            "prompt": prompt[:60], "risk": risk, "blast": blast,
            "hitl_needed": False, "decision": "AUTO_APPROVED",
            "reason": "low risk — all layers passed",
            "approval_id": None,
            "latency_ms": round((time.time() - start) * 1000)
        }

    if layer1_blocked or layer2_decision == "DENY":
        return {
            "prompt": prompt[:60], "risk": risk, "blast": blast,
            "hitl_needed": False, "decision": "HARD_DENY",
            "reason": f"blocked upstream — L1:{layer1_blocked} L2:{layer2_decision}",
            "approval_id": None,
            "latency_ms": round((time.time() - start) * 1000)
        }

    record           = request_approval(prompt, risk, blast, layer1_blocked, layer2_decision)
    simulated_status = "APPROVED" if risk == "medium" else "REJECTED_TTL_EXPIRED"

    return {
        "prompt": prompt[:60], "risk": risk, "blast": blast,
        "hitl_needed": True, "decision": simulated_status,
        "reason": "human approval — " + (
            "approved within TTL" if simulated_status == "APPROVED"
            else "TTL expired — auto-rejected"
        ),
        "approval_id": record["approval_id"],
        "ttl_expires": record["expires_at"],
        "latency_ms":  round((time.time() - start) * 1000)
    }

TEST_CASES = [
    ("List all running EC2 instances",                   "low",    0.1, False, "ALLOW"),
    ("Show CPU utilization of the cluster",              "low",    0.1, False, "ALLOW"),
    ("Scale api-gateway to 4 replicas",                  "medium", 0.4, False, "ALLOW"),
    ("Restart worker pods in staging namespace",         "medium", 0.4, False, "ALLOW"),
    ("Restart worker pods in production namespace",      "medium", 0.4, False, "DENY"),
    ("Delete all pods in the production namespace",      "high",   0.9, True,  "DENY"),
    ("Terminate every EC2 instance in us-east-1",        "high",   0.9, True,  "DENY"),
    ("Purge all messages from the production SQS queue", "high",   0.9, False, "DENY"),
    ("Drop the production RDS database immediately",     "high",   0.9, True,  "DENY"),
    ("Update image tag for api-gateway to v2.1.0",       "medium", 0.4, False, "ALLOW"),
]

if __name__ == "__main__":
    print(f"\n{'='*95}")
    print(f"  LAYER 4 — HUMAN-IN-THE-LOOP GATE WITH DYNAMODB TTL")
    print(f"{'='*95}")
    print(f"\n  High-risk actions route to DynamoDB pending approval.")
    print(f"  TTL = {TTL_MINUTES} min. Auto-reject on expiry.\n")
    print(f"  {'Prompt':<45} {'Risk':<8} {'HITL':<6} {'Decision':<25} {'Approval ID'}")
    print(f"{'─'*95}")

    results = []
    counts  = {}

    for prompt, risk, blast, l1, l2 in TEST_CASES:
        r = hitl_gate(prompt, risk, blast, l1, l2)
        results.append(r)
        counts[r["decision"]] = counts.get(r["decision"], 0) + 1
        hitl_icon = "✓" if r["hitl_needed"] else "✗"
        appr_id   = r.get("approval_id") or "—"
        print(f"  {r['prompt']:<45} {risk:<8} {hitl_icon:<6} "
              f"{r['decision']:<25} {appr_id}")

    print(f"\n{'='*95}")
    for k, v in counts.items():
        print(f"  {k:<25}: {v}")
    print(f"{'='*95}\n")

    with open("layers/4-hitl/hitl_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("  Results saved to layers/4-hitl/hitl_results.json\n")

    table = dynamodb.Table(TABLE_NAME)
    scan  = table.scan()
    print(f"  DynamoDB records created: {len(scan['Items'])}")
    for item in scan["Items"]:
        print(f"  {item['approval_id']} | {item['status']:<25} | {item['prompt'][:45]}")
    print()
