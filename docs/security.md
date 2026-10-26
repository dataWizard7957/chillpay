# Security

- Passwords use Argon2id. There are no default credentials.
- Sessions are stored as SHA-256 hashes. Cookie `chillpay_session` is HttpOnly and SameSite=Strict. `Secure` is on only when `CHILLPAY_SESSION_SECURE=true`.
- Idle timeout default 30 minutes. Absolute timeout default 8 hours.
- State-changing requests need header `X-CSRF-Token`. `/auth/me` issues a fresh CSRF token because only the hash is stored.
- The React app keeps the CSRF token in memory. It does not write secrets to local storage.
- PAN is stored only when `CHILLPAY_ENCRYPTION_KEY` and `CHILLPAY_LOOKUP_KEY` are both set and different. HR sees the full PAN. Others who can see the member see `XXXXXX` plus the last four digits.
- Aadhaar storage stays off.
- Finance is read-only. Finance cannot list members or payroll entries. Finance can read reports and invoices. TDS reports do not give finance a full PAN.
- Department heads approve their department and cannot approve their own entry. HR approves a regular entry only when the current head has an explicit uncancelled leave record.
- Audit metadata must not include salary, PAN, Aadhaar, comments, notes, or tokens.
- Backups contain personal and payroll data. Keep them off shared drives and restrict the backup folder.
