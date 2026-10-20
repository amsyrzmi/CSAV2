# Runbook 05 — Channel Credential Rotation (Not an Approved Procedure)

> **Status:** No generic, tested credential-rotation CLI or zero-downtime procedure
> is established here. Do not follow ad hoc SQL or call a provider from this document.
> Obtain an approved channel-specific procedure and use synthetic credentials in tests.

## Required safeguards for a future procedure

- Never put a plaintext credential in source, SQL, command-line arguments, URLs,
  terminal output, logs or fixtures.
- Use the exact column-specific AAD and encryption contract from current source;
  changing a value without matching AAD makes it undecryptable.
- Define ordering for provider-side rotation, application persistence, in-flight
  requests, rollback, revocation and audit before touching a live credential.
- Test with synthetic credentials and a disposable database. The development
  bootstrap helper is not a general credential-rotation tool.
- Verify safe failure and ensure no secret-bearing HTTP client logging.

Until a dedicated, reviewed procedure exists, treat credential rotation as blocked
and request an explicit channel-specific operational plan. No provider API example
is included here because token-bearing URLs can leak through shell/process history.
