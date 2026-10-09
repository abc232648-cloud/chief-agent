# Staff role contract

Chief exposes one canonical Staff presentation/workflow vocabulary: `Manager`, `Supervisor`, and `Worker`.

These Staff roles are **not** installation identity roles and their `staff.*` capabilities are **not** authorization permissions. They describe which workflow surfaces a Staff PWA may present. Every API operation still requires Chief identity authentication, domain scope, domain assignment and the operation-specific server-side authorization/state checks.

| Staff role | Scope metadata | Workflow capabilities |
| --- | --- | --- |
| Manager | `DOMAIN` | overview, SOP read, reporting, escalation, supervision, verification/correction, work assignment and management |
| Supervisor | `SUPERVISED` | overview, SOP read, reporting, escalation, supervision and verification/correction |
| Worker | `ASSIGNED` | overview, SOP read, reporting, escalation, work execution and submission |

The exact machine-readable IDs live in `agents/staff_roles.py`. The role objects identify their authority as `PRESENTATION_ONLY`.

## Identity authority remains separate

Chief's installation identity matrix remains unchanged. `Owner`, `Administrator`, `Manager` and `Worker` continue to determine coarse account permissions. In particular, `Supervisor` is not added as an installation-wide identity role.

Farm demonstrates the intended pattern: a Supervisor is normally a Chief `Worker` account with an authoritative Farm `SUPERVISOR` assignment. That assignment permits only the Farm workflows and task relationships enforced by the Farm backend. It does not grant `identity.workers.manage`, global settings access, another domain's data, or Manager-level installation authority.

A Manager's Staff workflow contract likewise does not itself grant `identity.workers.manage`; that permission comes only from the existing Chief `Manager` identity role and normal domain scoping.

## Manifest rule

Any agent exposing a `staff` interface must declare at least one canonical Staff role. Unknown roles such as `Owner`, `Administrator`, `Assistant Manager`, misspellings or arbitrary extension names fail closed at manifest construction. Agents may expose a subset of the three canonical roles.

Owner and companion interface audiences remain separate contracts and are not interpreted as Staff roles.
