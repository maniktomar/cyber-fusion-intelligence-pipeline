# The same Slack notification, with no custom code

The resolution summary in this project is roughly 150 lines of Python across
[`app/slack/`](../app/slack/) and
[`app/notifications/resolution.py`](../app/notifications/resolution.py), plus 58
tests. The same notification can be built in a Zapier Zap or a Make scenario in
about twenty minutes with no code at all.

This document is the honest comparison, because knowing *when not to write code*
is the actual skill. The short version: **most of what I built here should have
been low-code, and one specific part should not.**

---

## The no-code build

Both tools follow the same three-step shape.

### Zapier

| Step | Configuration |
|---|---|
| 1. Trigger | **Zendesk → New Ticket Event**, filtered to `status = solved` (Zapier has a native Zendesk integration; OAuth is a click-through, no client registration) |
| 2. Filter | **Only continue if** `Tags` contains `ai-triaged` — skips tickets this tool never touched |
| 3. Action | **Slack → Send Channel Message**, with fields mapped from the trigger payload |

Message template, using Zapier's field tokens:

```
:white_check_mark: Ticket #{{ticket__id}} resolved
Subject: {{ticket__subject}}
Category: {{ticket__tags}}
Opened: {{ticket__created_at}}
```

### Make

Same shape, different vocabulary: a **Zendesk → Watch Tickets** trigger module,
a **Filter** on `status = solved`, and a **Slack → Create a Message** module.
Make's visual mapper and its `formatDate` / `parseDate` functions handle the
timestamp formatting that Zapier needs a Formatter step for.

### Or no integration platform at all

Zendesk can do a large part of this natively: a **trigger** with conditions
`Status is Solved` and `Tags contains ai-triaged`, whose action is
**Notify active webhook**, pointed at a Slack Incoming Webhook URL with a
Liquid-templated JSON body. Zero third-party tools, zero recurring cost. See
[`zendesk/triggers/`](../zendesk/triggers/) for that version.

---

## What the no-code version cannot do

One field, and it happens to be the only field that matters.

**"Was the AI's draft actually used?"** is not present in any Zendesk webhook
payload. Computing it means fetching the ticket's full comment thread, finding
the internal note our own triage wrote, extracting the staged draft, comparing
it against the agent's public reply, and applying a similarity threshold — with
`UNKNOWN` as a distinct outcome for solved tickets that never got a public reply,
so duplicates and phone resolutions do not silently deflate the number.

In Zapier that is a Code step, three extra API-call steps, and multi-step
branching — at which point you have written the code anyway, but in a textarea
in a browser, with no tests, no version control, and no local reproduction.

Every other field — ticket ID, subject, category, resolution time — is a
straight mapping from the trigger payload. **They did not need code.**

---

## The honest verdict

| Field | Should have been | Why |
|---|---|---|
| Ticket ID, subject, category | Low-code | Direct payload mapping |
| Resolution time | Low-code | `created_at` to `updated_at`, one formatter step |
| **Draft-usage inference** | **Code** | Multi-call, stateful comparison with a tuned threshold and a three-state outcome |
| Message formatting | Low-code | Both tools have Slack Block Kit builders |

**Roughly three of the four fields did not justify the code I wrote for them.**
The right architecture for a real team is probably a hybrid: a Zap or a native
Zendesk trigger owns the notification, and calls one small custom endpoint for
the field that genuinely needs computing.

### When code is the right call anyway

- **The logic is the product.** The triage engine and its fallback layer are not
  a plumbing problem — there is no Zapier step for "score your own confidence
  and refuse to act below a threshold". That part is code and always was.
- **The behaviour needs testing.** 366 tests, 214 of them on failure paths, is
  not something a Zap gives you. For a notification, that does not matter. For
  the confidence gate, it is the whole point.
- **Failure has to be visible and specific.** A Zap that errors sends an email
  to its owner. A ticket that silently never got triaged needs a tag on the
  ticket saying so.
- **Volume changes the maths.** Per-task pricing is cheap at a hundred tickets a
  day and a real line item at fifty thousand.

### When low-code wins, and it wins often

- **A notification is a notification.** Nobody is unit-testing a Slack message.
- **Non-engineers can change it.** A support lead can edit the message wording
  or add a channel without a pull request, which means it actually gets
  maintained.
- **The integration is somebody else's problem.** Zapier's Zendesk connector
  handles OAuth, token refresh, retries, and API version changes. This project
  spent a stage on OAuth and still has an unresolved question about whether
  Zendesk issues refresh tokens.
- **It is running this afternoon.** The Zap took twenty minutes. Stage 7 did not.

### The trap in both directions

Reaching for code because it feels more rigorous produces 150 lines nobody
needed. Reaching for low-code because it is faster produces a Zap with four Code
steps, no tests, and business logic living in a browser textarea that nobody can
review or roll back.

The dividing line I would use: **if the logic has more than one interesting
failure mode, it belongs in code.** A Slack message either sends or does not.
The confidence gate has nine distinct ways to decline, each needing its own
explanation on the ticket. That is the difference, and it is why this project
has both.
