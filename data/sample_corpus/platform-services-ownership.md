---
title: Platform Services Ownership
acl: [public]
---
# Platform Services Ownership

## Service catalogue
The Payments Team owns the Payments API. The Payments API depends on the Ledger Database and the Fraud Detection Service.
The Data Platform Team owns the Customer-360 Pipeline. The Customer-360 Pipeline depends on the Orders Database and the Events Ingestion Service.
The Platform Engineering Team owns the Events Ingestion Service and the Identity Service.
The Risk Team owns the Fraud Detection Service. The Fraud Detection Service depends on the Identity Service.

## Governance
The Payments API is governed by the PCI Compliance Policy. The Customer-360 Pipeline is governed by the Data Retention Policy.
The Identity Service is governed by the Access Control Policy.

## Escalation
The On-call Engineer escalates to the Incident Commander. The Incident Commander escalates to the Head of Platform.
