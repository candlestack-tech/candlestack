#!/usr/bin/env bash
# Creates the CandleStack VM on AWS EC2 and boots it with cloud-init.yaml.
#
# Usage: infra/vm/aws-create.sh <tunnel-credentials.json> [git-ref]
#   tunnel-credentials.json  file written by `cloudflared tunnel create` (keep it secret)
#   git-ref                  branch or tag of this repo the VM clones (default: main)
#
# The instance gets no inbound rules: traffic arrives through the Cloudflare Tunnel,
# and the team reaches a shell with `aws ssm start-session --target <instance-id>`.
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: keep /aws/... and /dev/... arguments as they are

CREDENTIALS=${1:?usage: aws-create.sh <tunnel-credentials.json> [git-ref]}
GIT_REF=${2:-main}
NAME=candlestack-vm
INSTANCE_TYPE=${INSTANCE_TYPE:-t3.medium}
DISK_GB=25

# tr strips the CR that aws.exe adds to its output on Windows.
aws() { command aws --profile "${AWS_PROFILE:-candlestack}" --region "${AWS_REGION:-eu-north-1}" "$@" | tr -d '\r'; }

existing=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$existing" ]; then
  echo "Instance $NAME already exists: $existing" >&2
  exit 1
fi

# IAM role that lets Systems Manager open shell sessions on the instance.
if ! aws iam get-role --role-name "$NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$NAME" --output text --query Role.Arn \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  aws iam attach-role-policy --role-name "$NAME" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam create-instance-profile --instance-profile-name "$NAME" --output text --query InstanceProfile.Arn
  aws iam add-role-to-instance-profile --instance-profile-name "$NAME" --role-name "$NAME"
  echo "Waiting for the instance profile to propagate..."
  sleep 15
fi

# Security group without inbound rules (outbound stays open for the tunnel and updates).
vpc=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
sg=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$NAME" "Name=vpc-id,Values=$vpc" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$sg" = "None" ]; then
  sg=$(aws ec2 create-security-group --group-name "$NAME" --vpc-id "$vpc" \
    --description "CandleStack VM: no inbound, traffic via Cloudflare Tunnel" \
    --query GroupId --output text)
fi

ami=$(aws ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)

user_data=$(sed \
  -e "s|__TUNNEL_CREDENTIALS_B64__|$(base64 -w0 "$CREDENTIALS")|" \
  -e "s|__GIT_REF__|$GIT_REF|" \
  "$(dirname "$0")/cloud-init.yaml")

tags="Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=candlestack}]"
instance=$(aws ec2 run-instances \
  --image-id "$ami" \
  --instance-type "$INSTANCE_TYPE" \
  --iam-instance-profile "Name=$NAME" \
  --security-group-ids "$sg" \
  --credit-specification CpuCredits=standard \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$DISK_GB,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}" \
  --user-data "$user_data" \
  --tag-specifications "ResourceType=instance,$tags" "ResourceType=volume,$tags" \
  --query 'Instances[0].InstanceId' --output text)

echo "Launched $instance ($INSTANCE_TYPE, $ami). Waiting until it is running..."
aws ec2 wait instance-running --instance-ids "$instance"
echo "Running. cloud-init needs a few minutes; then check https://stage.candlestack.tech"
echo "Shell: aws ssm start-session --profile ${AWS_PROFILE:-candlestack} --region ${AWS_REGION:-eu-north-1} --target $instance"
