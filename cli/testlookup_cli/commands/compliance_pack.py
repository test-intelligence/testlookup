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

compliance_pack_app = typer.Typer(help="Compliance packs: generate, list and download a release's packs; verify a downloaded pack offline.")

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


# ── W18 DEV-01: generate / list / download (the same packs the release page lists) ──


def _resolved_release(release: str, project: Optional[str], profile_name: Optional[str]) -> str:
    import asyncio

    from testlookup_cli import output, release_resolver
    from testlookup_cli.errors import exit_code_for

    async def go():
        pid = await release_resolver.resolve_project(project, profile_name=profile_name)
        return await release_resolver.resolve_release(release, project_id=pid, profile_name=profile_name)

    try:
        return asyncio.run(go())
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


def _api(method: str, path: str, profile_name: Optional[str], **kw):
    import asyncio

    from testlookup_cli import client, output
    from testlookup_cli.errors import exit_code_for

    try:
        return asyncio.run(client.request(method, path, profile_name=profile_name, **kw))
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


@compliance_pack_app.command("list")
def list_packs(
    release: str = typer.Argument(..., help="Release id, name or version."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project id or name (narrows a release name)."),
    as_json: bool = typer.Option(False, "--json"),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
):
    """A release's compliance packs, newest first."""
    from testlookup_cli import output

    rid = _resolved_release(release, project, profile_name)
    packs = _api("GET", f"/api/v1/releases/{rid}/compliance-packs", profile_name) or []
    if as_json:
        output.print_json(packs)
        return
    rows = [{"id": p.get("id"), "generated": p.get("generated_at"), "files": p.get("file_count"),
             "bytes": p.get("bytes"), "manifest_sha256": (p.get("manifest_sha256") or "")[:16] + "…",
             "expires": p.get("retention_expires_at")} for p in packs]
    output.print_table(rows, ["id", "generated", "files", "bytes", "manifest_sha256", "expires"], title=f"Compliance packs ({len(rows)})")


@compliance_pack_app.command("generate")
def generate_pack(
    release: str = typer.Argument(..., help="Release id, name or version."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project id or name."),
    notes: Optional[str] = typer.Option(None, "--notes", help="A note stored with the pack."),
    retention_days: Optional[int] = typer.Option(None, "--retention-days", help="How long to keep it (server default when omitted)."),
    as_json: bool = typer.Option(False, "--json"),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
):
    """Build a signed compliance pack for the release (QA lead)."""
    from testlookup_cli import output

    rid = _resolved_release(release, project, profile_name)
    body = {k: v for k, v in {"notes": notes, "retention_days": retention_days}.items() if v is not None}
    pack = _api("POST", f"/api/v1/releases/{rid}/compliance-pack", profile_name, json_body=body, timeout=180.0)
    if as_json:
        output.print_json(pack)
        return
    output.print_success(
        f"Compliance pack {pack.get('id')} generated: {pack.get('file_count')} files, {pack.get('bytes')} bytes, "
        f"manifest sha256 {pack.get('manifest_sha256')}."
    )


@compliance_pack_app.command("download")
def download_pack(
    release: str = typer.Argument(..., help="Release id, name or version."),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project id or name."),
    pack_id: Optional[str] = typer.Option(None, "--pack", help="A pack id (default: the release's newest pack)."),
    out: Path = typer.Option(..., "--output", "-o", help="Where to write the ZIP."),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
):
    """Save a pack's ZIP: the same file the release page downloads. Verify it with `compliance-pack verify`."""
    import asyncio

    from testlookup_cli import client, output
    from testlookup_cli.errors import EXIT_NOT_FOUND, exit_code_for

    if not pack_id:
        rid = _resolved_release(release, project, profile_name)
        packs = _api("GET", f"/api/v1/releases/{rid}/compliance-packs", profile_name) or []
        if not packs:
            output.print_error(f"Release {release} has no compliance pack yet. Run `testlookup compliance-pack generate {release}`.")
            raise typer.Exit(EXIT_NOT_FOUND)
        pack_id = str(max(packs, key=lambda p: str(p.get("generated_at") or ""))["id"])
    try:
        data = asyncio.run(client.download(f"/api/v1/compliance-packs/{pack_id}/download", profile_name=profile_name, timeout=180.0))
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))
    out.write_bytes(data)
    output.print_success(f"Saved compliance pack {pack_id} to {out} ({len(data)} bytes).")
