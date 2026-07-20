# Engineering Production Runbook

## Deployment windows

Production deploys are allowed on Tuesday and Wednesday from 10:00 to 14:00
UTC. Friday deploys are blocked unless the VP Engineering grants an exception.
The annual release freeze runs from December 15 through January 5.

## Progressive delivery

New backend services must start with a 5 percent canary for 30 minutes. Feature
flags are managed in LaunchDarkly. The service owner must watch error rate,
p95 latency, and queue backlog before increasing rollout percentage.

## Incident response

For Sev1 incidents, the primary on-call engineer becomes incident commander
until relieved by the engineering manager. The Sev1 recovery time objective is
30 minutes, and the recovery point objective is 5 minutes. Updates must be
posted every 15 minutes in #incident-room.

## Rollback

The standard rollback command is deployctl rollback --service <service>
--last-good. If rollback fails, freeze the canary at the current percentage and
page the platform team through PagerDuty.

