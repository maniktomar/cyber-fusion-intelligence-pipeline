# Macros

## `insert-ai-draft.json`

Opens a public reply pre-filled with a greeting and the ticket reference, using
Liquid placeholders that resolve per ticket:

| Placeholder | Resolves to |
|---|---|
| `{{ticket.requester.first_name}}` | The customer's first name |
| `{{ticket.title}}` | The ticket subject |
| `{{ticket.id}}` | The ticket reference number |
| `{{current_user.first_name}}` | The agent applying the macro |

Two details worth defending:

**`| default: 'there'`** on the requester's name. Zendesk renders a missing
placeholder as an empty string, so a ticket from an address with no name
attached produces "Hi ," in a message to a customer. The filter is one token and
prevents the single most visible way a templated reply can embarrass you.

**The macro does not paste the AI draft itself.** It could not: a macro is
static configuration with no access to a ticket's comment thread, so there is no
Liquid expression that reaches into our internal note. But even if there were,
this project would not use it. The macro deliberately leaves a bracketed
instruction where the draft goes, so the agent has to open the note, read the
draft, and place it consciously. A macro that silently assembled a
customer-facing reply out of model output would put a one-click path between an
LLM and a customer, which is precisely what the rest of this codebase is built
to prevent.
