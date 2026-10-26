# Backup and restore

Backups are separate from the live database. Recommended frequency and retention are operational settings, not product defaults. Confirm them before production use.

## Backup

```
py -3.12 -m app.cli backup --output backups/manual
```

If `--output` is omitted, the folder is `backups/<UTC timestamp>` or `CHILLPAY_BACKUP_DIR/<timestamp>`.

The command copies the SQLite file with the SQLite backup API and copies invoice PDFs. A `manifest.json` records the time and invoice count.

## Restore

Restore only into a separate location:

```
py -3.12 -m app.cli restore --from backups/manual --database C:/temp/chillpay-restore.sqlite3 --invoices C:/temp/chillpay-invoices
```

Restore refuses `data/chill_pay.sqlite3`. Do not test restore by overwriting the live file.

After a restore, point `CHILLPAY_DATABASE_URL` and `CHILLPAY_INVOICE_DIR` at the restored paths and start the API.

## Protection

Treat backup folders as sensitive. They include member records, payroll snapshots, and invoices.
