"""``testlookup compliance-pack verify`` — verify a downloaded compliance pack offline (W11 REL-11).

Nothing is uploaded and no request is made: the ZIP, the deployment's public
key (``--public-key``, the PEM from ``GET /api/v1/compliance-packs/signing-keys``
or one a reviewer was given out of band) and, optionally, the manifest digest
recorded when the pack was generated (``--manifest-sha256``) are all local.

Exit codes (stable, for CI):

* ``0`` — verified: every file matches the manifest and the signature is valid,
  the pack is unsigned (tamper-evident only), or no ``--public-key`` was given
  (the signature is then not checked, and the output says so).
* ``1`` — tampered: a file does not match, an unlisted file is present, the
  manifest does not match ``--manifest-sha256``, the signature is invalid, or
  the pack is signed by another key than ``--public-key``; with
  ``--require-signature``, also an unsigned pack.
* ``2`` — the input could not be read (not a ZIP, a missing file, a public key
  that is not an Ed25519 PEM); Typer's usage errors keep 2 as well.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn, Optional

import typer

from testlookup_cli import pack_verify

compliance_pack_app = typer.Typer(help="Compliance packs: verify a downloaded pack offline.")

EXIT_VERIFIED = 0
EXIT_TAMPERED = 1
EXIT_UNREADABLE = 2


def _fail(message: str, code: int) -> NoReturn:
    typer.echo(message, err=code == EXIT_UNREADABLE)
    raise typer.Exit(code)


@compliance_pack_app.command("verify")
def verify(
    pack: Path = typer.Argument(..., help="The downloaded compliance pack ZIP."),
    public_key: Optional[Path] = typer.Option(
        None, "--public-key", help="The deployment's Ed25519 public key (PEM)."
    ),
    manifest_sha256: Optional[str] = typer.Option(
        None, "--manifest-sha256",
        help="The manifest digest recorded when the pack was generated (64 hex).",
    ),
    require_signature: bool = typer.Option(
        False, "--require-signature",
        help="Fail (exit 1) when the pack carries no signature: offline the CLI cannot "
             "know a pack WAS signed. Needs --public-key.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the verification as JSON."),
) -> None:
    """Verify a compliance pack ZIP against its manifest and signature, offline."""
    if require_signature and public_key is None:
        raise typer.BadParameter("--require-signature needs --public-key to check the signature.")

    try:
        zip_bytes = pack.read_bytes()
    except OSError as exc:
        _fail(f"UNREADABLE: could not read {pack}: {exc.strerror or exc}", EXIT_UNREADABLE)

    keys: dict[str, str] = {}
    given_key_id: Optional[str] = None
    if public_key is not None:
        try:
            pem = public_key.read_text(encoding="ascii")
            given_key_id, _ = pack_verify.load_public_key(pem)
        except (OSError, UnicodeDecodeError) as exc:
            _fail(f"UNREADABLE: could not read {public_key}: {exc}", EXIT_UNREADABLE)
        except pack_verify.PublicKeyError as exc:
            _fail(f"UNREADABLE: {exc}", EXIT_UNREADABLE)
        keys = {given_key_id: pem}

    result = pack_verify.verify_pack_bytes(
        zip_bytes,
        recorded_manifest_sha256=manifest_sha256,
        public_keys=keys,
    )

    if as_json:
        typer.echo(json.dumps(result, indent=2, default=str))

    if result["result"] == "unreadable":
        if not as_json:
            _fail(f"UNREADABLE: {pack} is not a ZIP file", EXIT_UNREADABLE)
        raise typer.Exit(EXIT_UNREADABLE)

    total = result["files_checked"]
    ok = result["files_ok"]
    signature = result["signature"]
    lines: list[str] = []
    code = EXIT_VERIFIED

    not_checked = public_key is None and signature == "unknown_key"
    if result["chain"] != "intact":
        code = EXIT_TAMPERED
        if result.get("reason") == pack_verify.REASON_MANIFEST_MISSING:
            lines.append("TAMPERED: manifest.json is missing from the pack")
        elif result.get("reason") == pack_verify.REASON_MANIFEST_UNREADABLE:
            lines.append("TAMPERED: manifest.json cannot be read")
        lines.extend(f"TAMPERED: {line}" for line in pack_verify.bad_file_lines(result))
    if result["manifest_matches_record"] is False:
        code = EXIT_TAMPERED
        lines.append("TAMPERED: the manifest does not match --manifest-sha256")
    if signature == "invalid":
        code = EXIT_TAMPERED
        lines.append("TAMPERED: the signature does not match this pack")
    elif signature == "unknown_key" and not not_checked:
        code = EXIT_TAMPERED
        lines.append(
            f"TAMPERED: the pack is signed by key {result['signing_key_id']}, "
            f"but --public-key is {given_key_id}"
        )
    elif signature == "unsigned" and require_signature and code == EXIT_VERIFIED:
        code = EXIT_TAMPERED
        lines.append(f"FAILED: {ok}/{total} files match, but the pack is unsigned (--require-signature)")

    if code == EXIT_VERIFIED:
        if signature == "valid":
            lines.append(f"OK: signature valid (key {result['signing_key_id']}), {ok}/{total} files match")
        elif not_checked:
            lines.append(f"OK: {ok}/{total} files match; signature not checked (no --public-key)")
        else:
            lines.append(f"OK: {ok}/{total} files match; the pack is unsigned (tamper-evident only)")

    if not as_json:
        for line in lines:
            typer.echo(line)
    raise typer.Exit(code)
