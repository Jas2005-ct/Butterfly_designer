<#
.SYNOPSIS
    One-time AWS setup for the ECS Express Mode deployment of Butterfly_designer.

.DESCRIPTION
    Creates (idempotently):
      1. the GitHub OIDC provider (so CI authenticates without static keys),
      2. the `github-actions-deploy` IAM role used by the workflows in
         .github/workflows/,
      3. the ECR repository that holds the application image.

    It does NOT create billable compute: no Express service, no ALB, no Fargate
    task. Those are created deliberately, after review (docs/deployment-plan.md).

.EXAMPLE
    pwsh ./infra/setup-aws-deploy-role.ps1
    pwsh ./infra/setup-aws-deploy-role.ps1 -Region ap-south-1
#>
[CmdletBinding()]
param(
    [string]$Region = 'ap-south-1',
    [string]$AccountId = '558948635923',
    [string]$Repository = 'butterfly-designer',
    [string]$RoleName = 'github-actions-deploy',
    [string]$GitHubOrgRepo = 'Jas2005-ct/Butterfly_designer'
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "    SKIP: $msg" -ForegroundColor DarkGray }

Write-Step "Checking caller identity"
$identity = aws sts get-caller-identity | ConvertFrom-Json
if ($identity.Account -ne $AccountId) {
    throw "You are acting as account $($identity.Account), expected $AccountId."
}
Write-Ok "account $AccountId (arn $($identity.Arn))"

# --- 1. GitHub OIDC provider -------------------------------------------------
Write-Step "GitHub OIDC provider"
$oidcArn = "arn:aws:iam::${AccountId}:oidc-provider/token.actions.githubusercontent.com"
$existing = aws iam list-open-id-connect-providers | ConvertFrom-Json
$hasOidc = $existing.OpenIdConnectProviderList | Where-Object { $_.Arn -eq $oidcArn }

if ($hasOidc) {
    Write-Skip "provider already exists"
} else {
    aws iam create-open-id-connect-provider `
        --url https://token.actions.githubusercontent.com `
        --client-id-list sts.amazonaws.com `
        --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 | Out-Null
    Write-Ok "provider created"
}

# --- 2. Deploy role ----------------------------------------------------------
Write-Step "IAM role $RoleName"
$trustPolicy = Get-Content (Join-Path $here 'oidc-trust-policy.json') -Raw
$roleJson = aws iam get-role --role-name $RoleName 2>$null | ConvertFrom-Json

if ($roleJson.Role) {
    Write-Skip "role already exists"
} else {
    aws iam create-role --role-name $RoleName --assume-role-policy-document $trustPolicy | Out-Null
    Write-Ok "role created"
}

# --- 3. Permissions ----------------------------------------------------------
Write-Step "Attaching ecs-deploy permissions"
aws iam put-role-policy `
    --role-name $RoleName `
    --policy-name ecs-deploy `
    --policy-document (Get-Content (Join-Path $here 'ecs-deploy-policy.json') -Raw) | Out-Null
Write-Ok "inline policy 'ecs-deploy' attached"

# --- 4. ECR repository -------------------------------------------------------
Write-Step "ECR repository $Repository"
$repo = aws ecr describe-repositories --repository-names $Repository --region $Region 2>$null | ConvertFrom-Json
if ($repo.repositories) {
    Write-Skip "repository already exists"
} else {
    aws ecr create-repository --repository-name $Repository --region $Region | Out-Null
    Write-Ok "repository created in $Region"
}

# --- 5. Service roles (needed by Express Mode) -------------------------------
Write-Step "ECS service roles"
$execRole = 'ecsTaskExecutionRole'
$infraRole = 'ecsInfrastructureRoleForExpressServices'

if (aws iam get-role --role-name $execRole 2>$null) {
    Write-Skip "$execRole already exists"
} else {
    aws iam create-role --role-name $execRole --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' | Out-Null
    aws iam attach-role-policy --role-name $execRole --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
    Write-Ok "$execRole created"
}

if (aws iam get-role --role-name $infraRole 2>$null) {
    Write-Skip "$infraRole already exists"
} else {
    aws iam create-role --role-name $infraRole --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs.amazonaws.com"},"Action":"sts:AssumeRole"}]}' | Out-Null
    aws iam attach-role-policy --role-name $infraRole --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
    Write-Ok "$infraRole created"
}

Write-Host "`nDone. IAM needs ~1 minute to propagate before CI can assume the role." -ForegroundColor Green
Write-Host "Next: build and push the image, then create the Express service (docs/deployment-plan.md section 6)." -ForegroundColor Green
