# Zendesk automation config

These are the Liquid-templated triggers and macros that sit on the Zendesk side.
They are configuration to paste into Admin Center, not code this service runs —
but they are part of the integration and belong in version control with it.

**Not yet applied to a live Zendesk account.** These are written against
documented Liquid placeholder syntax and reviewed, not round-tripped through a
real instance. Verifying them is part of stage 2.

| File | What it is | Where it goes |
|---|---|---|
| `triage-high-priority.json` | Trigger: fire the triage webhook only for urgent/high tickets | Admin Center → Objects and rules → Business rules → Triggers |
| `notify-resolution.json` | Trigger: fire the resolution webhook when an AI-triaged ticket is solved | Same |
| `../macros/insert-ai-draft.json` | Macro: paste the AI draft into a reply with the customer's name filled in | Admin Center → Workspaces → Macros |

## Why the priority filter exists

Every triage costs two LLM calls. Running it on every inbound ticket including
auto-replies, spam, and out-of-office bounces is most of the spend for none of
the value. The trigger restricts the webhook to `urgent` and `high` tickets,
which is a Zendesk-side filter — cheaper than an early return in our code,
because the request is never made at all.

It also excludes tickets already carrying an `ai-` tag. Our own write fires
`ticket.updated`, so without that condition the trigger and the service loop
each other indefinitely. The service has its own guard in
`ZendeskTicket.already_triaged`; this is the same defence one layer out, because
a loop that is stopped at the Zendesk boundary never becomes our traffic.
