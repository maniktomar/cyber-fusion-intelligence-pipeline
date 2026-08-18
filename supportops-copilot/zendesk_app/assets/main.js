/**
 * SupportOps Copilot - Zendesk ticket sidebar.
 *
 * Deliberately thin. The substantive logic (reading a decision back off the
 * ticket, deciding what state the sidebar is in) lives in the Python backend
 * where it is covered by tests; this file fetches one payload and renders it.
 * Every line of logic moved out of here is a line that gets tested.
 *
 * Two ZAF details that matter:
 *
 * - `client.request()` is used rather than `fetch()`. Zendesk proxies the call
 *   and signs it with the app's shared secret, which is what the backend
 *   verifies. A bare `fetch()` would be an unsigned cross-origin request and
 *   would need CORS opened up on the backend, which is the wrong trade.
 * - `client.invoke('ticket.comment.appendText', …)` appends to the reply box
 *   rather than replacing it, so an agent who has already started typing does
 *   not lose their work.
 */

const client = ZAFClient.init();

// The panel starts at a fixed height; content is measured after each render.
client.invoke("resize", { width: "100%", height: "180px" });

const app = document.getElementById("app");

const PRETTY_REASON = {
  empty_ticket: "The ticket had no usable text to read.",
  low_confidence_classification:
    "The model was not confident enough about the category or urgency.",
  low_confidence_draft:
    "The model was not confident enough in its suggested reply.",
  llm_unavailable: "The AI service could not be reached.",
  circuit_open: "The AI service is failing repeatedly, so calls are paused.",
  malformed_response: "The AI returned a response that could not be parsed.",
  model_refused: "The AI declined to process this ticket.",
  draft_rejected: "The suggested reply failed a safety check.",
  ungrounded_draft:
    "The suggested reply was not grounded in any knowledge base article.",
};

function showMessage(text) {
  app.replaceChildren();
  const p = document.createElement("p");
  p.className = "state-message";
  p.textContent = text;
  app.append(p);
  fitToContent();
}

function fitToContent() {
  // +8px so the last line is never clipped by the iframe border.
  const height = Math.min(document.body.scrollHeight + 8, 900);
  client.invoke("resize", { width: "100%", height: `${height}px` });
}

function setField(root, name, value) {
  const node = root.querySelector(`[data-field="${name}"]`);
  if (!node) return;
  // textContent, never innerHTML: `value` is model-generated text that reached
  // us through a ticket, which is the definition of untrusted input.
  node.textContent = value;
}

function asPercent(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "";
}

function renderTriaged(data) {
  const node = document
    .getElementById("tpl-triaged")
    .content.cloneNode(true);

  setField(node, "category", data.category || "unknown");
  setField(node, "urgency", data.urgency || "unknown");
  setField(node, "sentiment", data.sentiment || "unknown");
  setField(node, "reasoning", data.reasoning || "");
  setField(node, "draft", data.draft || "");
  setField(
    node,
    "classification-confidence",
    asPercent(data.classification_confidence)
  );
  setField(node, "draft-confidence", asPercent(data.draft_confidence));
  setField(
    node,
    "grounding",
    data.grounded_in && data.grounded_in.length
      ? `Grounded in: ${data.grounded_in.join(", ")}`
      : "Not grounded in any knowledge base article."
  );

  const button = node.getElementById
    ? node.getElementById("insert-draft")
    : node.querySelector("#insert-draft");

  if (button) {
    button.addEventListener("click", () => insertDraft(button, data.draft));
    if (!data.draft) button.disabled = true;
  }

  app.replaceChildren(node);
  fitToContent();
}

function insertDraft(button, draft) {
  if (!draft) return;
  button.disabled = true;
  button.textContent = "Inserting…";

  client
    .invoke("ticket.comment.appendText", draft)
    .then(() => {
      button.textContent = "Inserted into reply";
    })
    .catch(() => {
      // Never leave the button stuck on "Inserting…" - an agent would not know
      // whether it worked, and would either retry or give up on the tool.
      button.disabled = false;
      button.textContent = "Insert failed - try again";
    });
}

function renderFlagged(data) {
  const node = document
    .getElementById("tpl-flagged")
    .content.cloneNode(true);
  setField(
    node,
    "fallback-reason",
    PRETTY_REASON[data.fallback_reason] ||
      "The copilot could not process this ticket."
  );
  setField(node, "fallback-detail", data.fallback_detail || "");
  app.replaceChildren(node);
  fitToContent();
}

function render(data) {
  switch (data.state) {
    case "triaged":
      return renderTriaged(data);
    case "needs_manual_triage":
      return renderFlagged(data);
    case "not_triaged":
      return showMessage("This ticket has not been triaged by the copilot.");
    default:
      return showMessage(
        data.fallback_detail ||
          "This ticket's triage note could not be read. Open the internal note to see it."
      );
  }
}

async function load() {
  try {
    const settings = await client.metadata();
    const ticket = await client.get("ticket.id");
    const ticketId = ticket["ticket.id"];
    const backend = String(settings.settings.backendUrl || "").replace(
      /\/+$/,
      ""
    );

    const response = await client.request({
      url: `${backend}/api/sidebar/tickets/${ticketId}/triage`,
      type: "GET",
      // Zendesk signs the request with the app's shared secret; the backend
      // verifies that signature. This is why the app declares `secure` params.
      secure: true,
      httpCompleteResponse: true,
      contentType: "application/json",
    });

    render(response.responseJSON || JSON.parse(response.responseText));
  } catch (error) {
    // A backend that is down must not render a blank panel. An agent seeing
    // nothing assumes the tool is broken and stops looking at it entirely.
    showMessage(
      "Could not reach the copilot backend. Triage may still have run - check the internal notes."
    );
  }
}

load();
