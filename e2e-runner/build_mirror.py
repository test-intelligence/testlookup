"""Select a reachable signed Ubuntu archive on the build host before starting the builder."""

import http.client
import socket
import ssl
import sys


def select_mirror():
    # APT independently verifies Ubuntu's archive signature on every pocket.
    # kernel.org operates a public Ubuntu package mirror at /ubuntu.
    hosts = ("archive.ubuntu.com", "azure.archive.ubuntu.com", "mirrors.edge.kernel.org")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    for host in hosts:
        try:
            addresses = socket.getaddrinfo(
                host, 443, socket.AF_INET, socket.SOCK_STREAM
            )
        except OSError:
            print(f"{host}: build host DNS unavailable", file=sys.stderr)
            continue
        for address in dict.fromkeys(record[4][0] for record in addresses):
            try:
                with socket.create_connection((address, 443), timeout=5) as stream:
                    with context.wrap_socket(stream, server_hostname=host) as tls:
                        conn = http.client.HTTPSConnection(host, context=context)
                        conn.sock = tls
                        conn.request("GET", "/ubuntu/dists/noble-security/InRelease")
                        with conn.getresponse() as response:
                            if (
                                response.status == 200
                                and response.readline(128).strip()
                                == b"-----BEGIN PGP SIGNED MESSAGE-----"
                            ):
                                print(
                                    f"{host}: build host verified TLS and found noble-security InRelease",
                                    file=sys.stderr,
                                )
                                return host, address
            except (OSError, http.client.HTTPException):
                continue
        print(
            f"{host}: build host DNS resolved, signed index unavailable over HTTPS",
            file=sys.stderr,
        )
    raise RuntimeError(
        "No tested Ubuntu archive provided noble-security InRelease to the build host"
    )


if __name__ == "__main__":
    hostname, address = select_mirror()
    print("build_arg=UBUNTU_ARCHIVE_HOST=" + hostname)
    print("host_mapping=" + hostname + ":" + address)
