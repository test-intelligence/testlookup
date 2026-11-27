# Independent custody of audit exports

The standalone operator command `python -m testlookup_cli.audit_archive`
preserves an already-authorized local export under an independent Ed25519
custody signature. Its optional publisher writes a new, versioned, encrypted
Object Lock object using a separately provisioned archive-writer role. It
does not connect to the application database, read application configuration,
download source data, run on the application scheduler, or delete evidence.
Provisioning and any real storage write require the site's normal operator
authorization; none are performed by the repository tests.

This is a protective custody path for **captured projections**. It is not a
complete operational-history collector or a replacement for source audit
controls. Keep the application writer, authorized source exporter, independent
archive signer/writer, audit reader and retention operator distinct. Do not
mount the custody private key or archive credentials in API/worker containers,
reuse `APP_SECRET_KEY`, reuse the application's compliance signing key, or
place the archive under an application-controlled MinIO root credential.
Use an independent storage account/administrative boundary and retain its
public-key fingerprint and publication receipts outside the application.

## Existing record and retention contracts

`audit_log_service.record_outcome` stages a settings audit row in the business
transaction; a rollback removes that outcome. `record_attempt` uses a separate
session and retains the attempt across a caller rollback. Access-change audit
writes stage on the caller's session. This custody command changes none of
these semantics, source table grants, or database ownership.

Operational SQL/Mongo audit records, compliance evidence and custody copies
have different lifecycles. Source retention currently removes some project
audit classes and expired exports, respects applicable project/run/release/
pack holds, and retains settings audit records. Existing compliance packs
and retention-run archives already use one SHA-256 manifest and Ed25519
signature format. That application signing key is encrypted beside the
database under `APP_SECRET_KEY`; compromising both can forge future source
exports. The independent countersignature anchors what the operator captured
at a time, rather than proving an honest or complete source database.

The supported inputs are:

| Source kind | Input and trust contract |
|---|---|
| `audit_csv` | Existing ADMIN-only `/api/v1/audit-dashboard/export` CSV, with its exact five redacted projection columns. Operator attests authorization and capture scope. It has no source signature or stable event IDs. |
| `compliance_pack` | Existing signed release pack with trusted application public key and independently recorded manifest digest. Unsigned, altered, wrong-key or out-of-scope packs are refused. |
| `retention_archive` | Existing signed `scope="runs"` archive using the same manifest/signature verifier. Subject/project portability exports are deliberately unsupported here. |

The CSV endpoint caps its merged result at **5,000 rows**, has a relative-days
filter, and omits original event IDs and detail fields. A CSV reaching that
cap is refused, because completeness is ambiguous. A smaller CSV still does
not prove full history. Review project/category/window coverage before every
capture; high-volume or stable-ID recovery requires a separate authorized
export design. Missing source events before archival, sampling/omissions in a
source pack and deletion of an uncatalogued archive cannot be reconstructed
by a signature. Every custody manifest explicitly says
`completeness="captured_projection_only"`.

## Local sealing and offline verification

Use an isolated operator environment with the existing TestLookup CLI. Only
the optional publisher additionally needs the backend-compatible SDK pins:

```sh
python -m pip install --require-hashes -r cli/requirements-audit-archive.txt
```

The SDK file is a small dependency subset, not an installation of the backend.
Keep it synchronized with the backend lock when upgrading that SDK. No new
application setting or database migration is required.

Generate or obtain a dedicated Ed25519 key using the site's secret-management
process. Keep its private PEM readable only by the signer account; POSIX
group/world-readable key files are refused. Windows deployments must enforce
equivalent ACLs outside Python's permission-bit checks. Keep the public key
and fingerprint independently pinned; a public key copied from the same
untrusted archive is not a trust anchor. The CLI never generates production
keys, stores them in application tables or prints key material.

Prepare a small scope JSON containing exactly:

```json
{"kind":"project","project_id":"11111111-1111-4111-8111-111111111111","window_start":"2026-11-26T00:00:00+00:00","window_end":"2026-11-27T00:00:00+00:00"}
```

Use `kind="instance", project_id=null` only for an authorized instance-wide
export. Project captures reject CSV rows or signed evidence for another
project. For a CSV, the window covers row timestamps. For a signed pack/run
archive, it covers the export's signed generation timestamp; it does not
claim that all contained historical facts occurred within that window.
Use opaque scope identifiers, not names, credentials or destination URLs.

```sh
python -m testlookup_cli.audit_archive seal --input audit.csv --source-kind audit_csv --scope scope.json --retention-until 2028-01-01T00:00:00Z --private-key custody-private.pem --output custody.zip
python -m testlookup_cli.audit_archive verify --input custody.zip --public-key custody-public.pem --expected-sha256 <independently-recorded-object-sha256>
python -m testlookup_cli.audit_archive recover --input custody.zip --public-key custody-public.pem --expected-sha256 <independently-recorded-object-sha256> --output recovered-export
```

For signed source kinds also provide `--source-public-key` and
`--source-manifest-sha256`, obtained and pinned through the existing evidence
process. The application key and independent custody key must differ.
Retain old public keys after rotation so old captures remain verifiable.
Rotation of a private key must not replace an old public trust anchor.

Sealing is bounded to 64 MiB source bytes; source evidence verification also
bounds member count and uncompressed size. Output contains only fixed
`payload`, `manifest.json`, and `signature.json` members, with no extraction
or caller-controlled paths. The signature binds the payload digest, scope,
capture time, archive UUID, provenance check and requested retention window.
The final ZIP checksum printed at sealing binds the complete transport
object when saved independently. Output creation is exclusive and mode 0600
on POSIX; an existing output is never overwritten. A partial local file from
an interrupted write must fail verification and be handled as an incomplete
capture. Protect plaintext source/output files with the operator's encrypted
storage and ACLs; delete temporary copies only under the applicable policy.

## Independent storage and role guard

[`iam-policies.example.json`](../../infra/audit-archive/iam-policies.example.json)
contains separate example identity policies and a bucket guard. It is not
one directly deployable policy. Replace every `__PLACEHOLDER__`, choose
retention bounds and KMS key/region, validate rendered policies against the
provider, and have the storage owner review them before provisioning. The
`kms:ViaService` example targets commercial AWS; adjust its DNS suffix for
other partitions. Use dedicated roles without broader inherited policies,
bucket-admin rights, object ACL rights or governance-bypass permissions.

| Credential/role | Intended capability |
|---|---|
| Application writer | Explicitly denied access to the independent archive bucket. Existing source DB permissions are unchanged. |
| Archive writer | Conditional, encrypted new-object write; place COMPLIANCE retention and hold ON; read protection metadata; generate KMS data key. No object-body read, decrypt, deletion or hold removal. |
| Audit reader | Retrieve a known version and its protection metadata, and decrypt with the dedicated KMS key. No write or retention mutation. |
| Retention operator | Read protection metadata, deliberately release/restore holds, delete an eligible **version**. No source/application mutation, archive write, body read or decrypt. |

Enable bucket versioning, Object Lock and the example public-access block.
The bucket guard refuses insecure transport, application principals,
unconditional writes, the wrong encryption key/mode and non-COMPLIANCE
retention. Hold removal is reserved for the retention role. It denies
unversioned `DeleteObject` operations, preventing delete markers from hiding
locked history; maintenance uses explicit version IDs. Choose the legal
retention period before sealing: COMPLIANCE retention cannot be shortened,
including by the account root user. Allow at least an additional day beyond
the configured minimum before upload so elapsed capture/transfer time does
not cross a remaining-retention-days policy boundary.

The explicit publisher command is intended for an independently provisioned
AWS role profile, not the application's environment/default credential chain:

```sh
python -m testlookup_cli.audit_archive publish --input custody.zip --public-key custody-public.pem --archive-profile independent-archive-writer --writer-role-arn <expected-archive-writer-role-arn> --bucket <archive-bucket> --owner-account <12-digit-archive-account> --kms-key-arn <full-dedicated-key-arn> --region <aws-region>
```

The publisher verifies the capture first, requires the expected assumed role
and bucket owner, ignores endpoint overrides, checks versioning/Object Lock,
then uses one conditional `PutObject` with `If-None-Match: *`, SHA-256 checksum,
SSE-KMS, COMPLIANCE expiry and legal hold **ON**. It verifies the acknowledged
checksum/encryption/version and reads retention/hold metadata back for that
specific version. It never retries with an unprotected overwrite and offers
no hold-release/delete command. S3-compatible providers are not an assumed
drop-in: this command intentionally uses the AWS SDK's AWS endpoints and
must not be pointed at the application's shared MinIO.

Save the receipt's key, version ID, complete-object SHA-256 and signing-key
fingerprint in an independently governed catalog. Regularly reconcile that
catalog against known object versions. On any interrupted/failed publish,
treat outcome as **uncertain**: the object may already exist and be locked.
Reconcile it through the archive owner before retrying. Do not delete it,
shorten protection, reuse a different key to conceal the attempt or fall
back to the application storage credentials.

## Holds, authorized retention and recovery

Custody hold ON is a conservative intake hold, independent of source holds.
Application retention or an administrator retiring a source pack does not
authorize deleting the custody copy. The custody command does not poll,
extend, mirror or release source holds. The retention operator must obtain
current source project/run/release/pack hold state and the applicable legal
policy from an authorized reviewer, record approval against the exact archive
UUID/version, and verify the provider's protection metadata before any hold
release. Retain hold-release/deletion approvals independently from the
application and archive writer. If that source/approval check is unavailable,
leave the hold ON. Active legal holds remain indefinite until explicitly
released; an expired COMPLIANCE date alone is insufficient.

After the COMPLIANCE deadline, after all source/custody holds are released,
and after the approved retention decision, a retention-only role may delete
the exact version using the provider's supported maintenance operation.
Re-read both protection metadata and the deletion outcome and update the
independent catalog. Do not delete by current key without a version ID.
This workflow requires a site drill; the repository does not automate or
execute it.

For recovery, the audit reader retrieves the receipt's exact version to
protected local scratch, then verifies it against the independently retained
public key and object SHA-256. The offline `recover` command requires that
receipt checksum and writes the verified original payload to a fresh local
file without overwriting an existing path. Preserve those source bytes when
handing them to the existing offline compliance verifier. This demonstrates
evidence readback, not database restore: existing retention exports explicitly
cannot be re-imported into TestLookup. Source business data restoration
continues to use the existing backup/restore process.

Readback also depends on preserving the KMS key and public trust anchors.
Object Lock does not supply key backup, KMS deletion controls or recovery
from storage-account control-plane compromise; those remain independent
storage-owner responsibilities.

## Validation and acceptance still open

The focused tests in
[`test_audit_archive_custody.py`](../../backend/tests/regression/test_audit_archive_custody.py)
exercise independent signatures, payload/scope/signature tampering, strict
existing source verification, scope/cap refusal, exclusive CLI output,
application-role refusal, SDK request shapes, encryption/version/hold
readback failures and local recovered-copy verification. S3 responses are
Botocore stubs. These tests do **not** prove AWS IAM enforcement or WORM on a
real provider. Existing product signing/hold/retention tests remain separate
requirements and their source behavior is unchanged.

S03 acceptance remains open for real independent account/role provisioning,
application mutation refusal against that provider, provider hold and
COMPLIANCE expiry/deletion controls, signed receipt/catalog reconciliation,
an authorized recovery drill, complete operational-source coverage and any
desired source DB role separation. The source exporter currently requires
ADMIN; dedicated read-only export authority is a separate product change.
Do not mark operational audit history globally immutable or claim compliance
certification from this custody path.

Primary guidance: [existing source signing format](../../backend/app/services/compliance_signing.py),
[existing offline verifier](../../cli/testlookup_cli/pack_verify.py),
[AWS conditional-write policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html),
[Object Lock and holds](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html),
[Object Lock maintenance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-managing.html),
and [S3 action/condition support](https://docs.aws.amazon.com/service-authorization/latest/reference/list_s3.html).
