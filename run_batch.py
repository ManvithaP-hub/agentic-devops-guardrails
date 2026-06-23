import boto3, json, uuid, time, os

_REGION  = os.environ.get("AWS_REGION", "us-east-1")
bedrock  = boto3.client("bedrock",         region_name=_REGION)
s3       = boto3.client("s3",              region_name=_REGION)
runtime  = boto3.client("bedrock-runtime", region_name=_REGION)

ACCOUNT  = boto3.client("sts").get_caller_identity()["Account"]
BUCKET   = f"devops-agent-snapshots-{ACCOUNT}"
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

# Create S3 bucket for batch if not exists
try:
    s3.create_bucket(Bucket=BUCKET)
    print(f"Created bucket: {BUCKET}")
except:
    print(f"Bucket exists: {BUCKET}")

# Build batch input from 100-prompt dataset
from layers.enterprise_dataset import PROMPTS

records = []
for category, items in PROMPTS.items():
    for prompt, risk, blast in items:
        records.append({
            "recordId": str(uuid.uuid4()),
            "modelInput": {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}]
            }
        })

# Write JSONL input file
input_key = "batch-input/prompts.jsonl"
jsonl = "\n".join(json.dumps(r) for r in records)
s3.put_object(Bucket=BUCKET, Key=input_key, Body=jsonl.encode())
print(f"Uploaded {len(records)} records to s3://{BUCKET}/{input_key}")

# Create batch inference job
role_arn = f"arn:aws:iam::{ACCOUNT}:role/devops-agent-lambda-role"

try:
    job = bedrock.create_model_invocation_job(
        jobName        = f"guardrail-eval-{int(time.time())}",
        modelId        = MODEL_ID,
        inputDataConfig  = {"s3InputDataConfig": {
            "s3Uri":        f"s3://{BUCKET}/{input_key}",
            "s3InputFormat": "JSONL"
        }},
        outputDataConfig = {"s3OutputDataConfig": {
            "s3Uri": f"s3://{BUCKET}/batch-output/"
        }},
        roleArn = role_arn
    )
    print(f"Batch job created: {job['jobArn']}")
    print(f"Job name: {job['jobArn'].split('/')[-1]}")
    print("\nCheck status:")
    print(f"aws bedrock get-model-invocation-job --job-identifier \"{job['jobArn']}\" --region us-east-1")
except Exception as e:
    print(f"Batch error: {e}")
    print("\nFallback: request quota increase at:")
    print("https://us-east-1.console.aws.amazon.com/servicequotas/home/services/bedrock/quotas")
