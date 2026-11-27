#!/usr/bin/env python3
"""Render bounded opt-in HTTPS/mTLS infrastructure scrape and inventory files.

This command validates declared site configuration offline. It performs no DNS
lookup, network request, exporter installation or certificate read.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml


IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}\Z")
NAMESPACE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
DNS_NAME = re.compile(r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
COMPOSE_TLS = "/etc/prometheus/infrastructure"
OPERATOR_TLS = "/etc/prometheus/secrets/testlookup-infrastructure-client"
SCOPE = ("cluster", "namespace", "telemetry_profile", "telemetry_role", "job", "instance")
ROLES = {"host-exporter":"testlookup-infra-host", "postgres-exporter":"testlookup-infra-postgres"}
METRICS = {
    "host-exporter":("node_filesystem_avail_bytes", "node_filesystem_size_bytes", "node_vmstat_oom_kill"),
    "postgres-exporter":("pg_up", "pg_settings_max_connections"),
}


class ConfigurationError(ValueError):
    """A finite diagnostic containing no supplied values or private endpoints."""


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise ConfigurationError("duplicate or non-string configuration key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _private_address(address: object) -> None:
    if not isinstance(address, str) or len(address) > 260 or any(character.isspace() for character in address):
        raise ConfigurationError("invalid exporter address")
    try:
        parsed = urlsplit("https://" + address)
        host, port = parsed.hostname, parsed.port
        if (not host or not port or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or not 1 <= port <= 65535):
            raise ValueError
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            if not DNS_NAME.fullmatch(host):
                raise ValueError from None
        else:
            allowed = literal.is_loopback or any(literal in network for network in (
                ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"),
            ) if literal.version == network.version)
            if not allowed:
                raise ValueError
    except (ValueError, TypeError):
        raise ConfigurationError("expected a private host:port without URL components") from None


def validate_site(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema_version", "profile", "cluster", "namespace", "monitoring_namespace", "targets"}:
        raise ConfigurationError("unexpected site configuration fields")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["profile"], str) or value["profile"] not in {"compose", "kubernetes"}):
        raise ConfigurationError("unsupported infrastructure profile")
    for field, pattern in (("cluster", IDENTITY), ("namespace", NAMESPACE), ("monitoring_namespace", NAMESPACE)):
        if not isinstance(value[field], str) or not pattern.fullmatch(value[field]):
            raise ConfigurationError("invalid site scope")
    targets = value["targets"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 16:
        raise ConfigurationError("declare one to sixteen infrastructure targets")
    identities = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"instance", "role", "address", "server_name", "private_endpoint"}:
            raise ConfigurationError("unexpected target fields")
        if not isinstance(target["role"], str) or target["role"] not in ROLES or target["private_endpoint"] is not True:
            raise ConfigurationError("declare a supported private exporter role")
        if not isinstance(target["instance"], str) or not IDENTITY.fullmatch(target["instance"]):
            raise ConfigurationError("invalid stable exporter identity")
        identity = (target["role"], target["instance"])
        if identity in identities:
            raise ConfigurationError("duplicate exporter identity")
        identities.add(identity)
        _private_address(target["address"])
        if not isinstance(target["server_name"], str) or not DNS_NAME.fullmatch(target["server_name"]):
            raise ConfigurationError("a certificate DNS server name is required")
    result = deepcopy(value)
    result["targets"].sort(key=lambda target:(target["role"],target["instance"]))
    return result


def load_site(path: Path) -> dict:
    with path.open("rb") as stream:
        content = stream.read(65_537)
    if len(content) > 65_536:
        raise ConfigurationError("site configuration exceeds its size budget")
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise ConfigurationError("site configuration must be UTF-8") from None
    loader = _UniqueLoader(text)
    try:
        return validate_site(loader.get_single_data())
    finally:
        loader.dispose()


def target_labels(site: dict, target: dict) -> dict[str, str]:
    return {"cluster":site["cluster"],"namespace":site["namespace"],"telemetry_profile":site["profile"],
            "telemetry_role":target["role"],"job":ROLES[target["role"]],"instance":target["instance"]}


def render_scrapes(site: dict) -> dict:
    site = validate_site(site)
    tls = OPERATOR_TLS if site["profile"] == "kubernetes" else COMPOSE_TLS
    jobs = []
    for target in site["targets"]:
        role = target["role"]
        labels = target_labels(site, target)
        keep = "|".join(METRICS[role])
        relabel = [{"source_labels":["__name__"],"regex":keep,"action":"keep"}]
        if role == "host-exporter":
            relabel.append({"source_labels":["__name__","mountpoint"],
                            "regex":"node_filesystem_(avail|size)_bytes;/|node_vmstat_oom_kill;","action":"keep"})
        allowed_labels = ("__name__",*SCOPE,*( ("mountpoint",) if role == "host-exporter" else () ))
        relabel.append({"regex":"|".join(allowed_labels),"action":"labelkeep"})
        jobs.append({
            "job_name":labels["job"]+"-"+target["instance"],"scheme":"https","metrics_path":"/metrics",
            "scrape_interval":"30s","scrape_timeout":"5s","honor_labels":False,"honor_timestamps":False,
            "follow_redirects":False,"proxy_from_environment":False,"enable_http2":False,
            "tls_config":{"ca_file":tls+"/ca.crt","cert_file":tls+"/client.crt","key_file":tls+"/client.key",
                          "server_name":target["server_name"],"insecure_skip_verify":False,"min_version":"TLS12"},
            "static_configs":[{"targets":[target["address"]],"labels":labels}],
            "sample_limit":128,"target_limit":1,"body_size_limit":"2MB","label_limit":10,
            "label_name_length_limit":64,"label_value_length_limit":128,"metric_relabel_configs":relabel,
        })
    return {"scrape_configs":jobs}


def render_contract(site: dict) -> dict:
    site = validate_site(site)
    return {"groups":[{"name":"testlookup.infrastructure_contract."+site["profile"],"interval":"30s","rules":[
        {"record":"testlookup:infrastructure_expected_target","expr":"vector(1)","labels":target_labels(site,target)}
        for target in site["targets"]]}]}


def render_operator(site: dict) -> list[dict]:
    site = validate_site(site)
    if site["profile"] != "kubernetes":
        raise ConfigurationError("Operator output requires the kubernetes profile")
    metadata = {"namespace":site["monitoring_namespace"],"labels":{"app.kubernetes.io/name":"testlookup",
                "app.kubernetes.io/part-of":"testlookup"}}
    return [
        {"apiVersion":"v1","kind":"Secret","metadata":{**deepcopy(metadata),"name":"testlookup-infrastructure-scrapes"},
         "type":"Opaque","stringData":{"scrapes.yml":yaml.safe_dump(render_scrapes(site)["scrape_configs"],sort_keys=False)}},
        {"apiVersion":"monitoring.coreos.com/v1","kind":"PrometheusRule",
         "metadata":{**deepcopy(metadata),"name":"testlookup-infrastructure-contract",
                     "labels":{**metadata["labels"],"role":"alert-rules"}},"spec":render_contract(site)},
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--scrape-out", type=Path, required=True)
    parser.add_argument("--rules-out", type=Path, required=True)
    parser.add_argument("--operator-out", type=Path)
    args = parser.parse_args(argv)
    try:
        outputs = [args.scrape_out,args.rules_out] + ([args.operator_out] if args.operator_out else [])
        if len({path.resolve() for path in [args.site,*outputs]}) != len(outputs)+1:
            raise ConfigurationError("input and output files must be distinct")
        site = load_site(args.site)
        rendered = [yaml.safe_dump(render_scrapes(site),sort_keys=False),yaml.safe_dump(render_contract(site),sort_keys=False)]
        if args.operator_out:
            rendered.append(yaml.safe_dump_all(render_operator(site),sort_keys=False))
        for path,content in zip(outputs,rendered):
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(content,encoding="utf-8")
    except (ConfigurationError, OSError, TypeError, ValueError, yaml.YAMLError):
        parser.exit(2,"Infrastructure metrics configuration failed; check the declared private targets, scope and output paths.\n")
    print("Generated bounded infrastructure scrapes and independent expected-target rules; no endpoint contacted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
