import { type FormEvent, useState } from "react";
import { clearApiKey, setApiKey } from "../api/apiKey";
import { useHasApiKey } from "../api/useHasApiKey";
import { useRemovedReason } from "../api/useRemovedReason";

// Visible ASCII without spaces: the characters the API allows in a key (_API_KEY_PATTERN in
// src/bioparser/api/config.py). Anything else, such as a zero-width space pasted from a chat
// app, cannot be sent in an HTTP header at all.
const VALID_KEY = /^[\x21-\x7e]+$/;

export function ApiKeyPage() {
  const hasApiKey = useHasApiKey();
  return (
    <>
      <h1>API key</h1>
      {hasApiKey ? <StoredKey /> : <KeyForm />}
    </>
  );
}

function StoredKey() {
  return (
    <>
      <p>An API key is stored. The UI sends it with every request and never shows it.</p>
      <button type="button" onClick={() => clearApiKey("forgotten")}>
        Forget key
      </button>
    </>
  );
}

function KeyForm() {
  const removedReason = useRemovedReason();
  const [invalid, setInvalid] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    // Stop the browser from sending the form itself. That would put the key in a request
    // to /ui, or with the default GET method, in the URL.
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const value = data.get("apiKey");
    const key = typeof value === "string" ? value.trim() : "";
    if (!VALID_KEY.test(key)) {
      setInvalid(true);
      return;
    }
    const remember = data.get("remember") === "on";
    // Empty the field, so the key does not stay on the page.
    form.reset();
    setApiKey(key, remember);
  }

  return (
    <>
      {removedReason === "rejected" && (
        <p role="alert" className="key-message">
          The API did not accept the key. Enter it again.
        </p>
      )}
      {removedReason === "elsewhere" && (
        <p role="status" className="key-message">
          The key was removed in another tab.
        </p>
      )}
      {removedReason === "forgotten" && (
        <p role="status" className="key-message">
          The key was forgotten.
        </p>
      )}
      {invalid && (
        <p role="alert" className="key-message">
          This is not a valid API key. A key has only letters, digits and symbols, with no spaces.
        </p>
      )}
      {/* method="post": if the browser ever sends the form itself, the key is not in the URL. */}
      <form method="post" onSubmit={handleSubmit} className="key-form">
        <label className="key-field">
          API key{" "}
          <input type="password" name="apiKey" required autoComplete="off" aria-invalid={invalid} />
        </label>
        <label>
          <input type="checkbox" name="remember" /> Remember on this device
        </label>
        <button type="submit">Save key</button>
      </form>
    </>
  );
}
