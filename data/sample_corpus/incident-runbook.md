---
title: Production Incident Runbook
acl: [engineering]
---
# Production Incident Runbook

## Severity levels
SEV1 is a full outage or data loss affecting customers; SEV2 is a major degradation with a workaround; SEV3 is a minor issue with no customer impact.

## Response times
For SEV1 the On-call Engineer must acknowledge the page within 5 minutes and open an incident channel within 10 minutes. For SEV2 acknowledgement is required within 15 minutes.

## Roles
The Incident Commander coordinates the response, owns communications and decides on rollback. Status page updates are posted every 30 minutes during a SEV1.

## Post-incident review
A blameless post-incident review is held within 5 working days of every SEV1 and SEV2, and action items are tracked in the engineering backlog.
