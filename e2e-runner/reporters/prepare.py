"""Build native reporter fixtures before entering the isolated test network."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREPARED = HERE / "prepared"


def run(tool, *args, cwd=HERE):
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"Missing producer build tool: {tool}")
    subprocess.run([path, *map(str, args)], cwd=cwd, check=True)


def javascript():
    sdk = ROOT / "client/js"
    run("npm", "ci", "--no-audit", "--no-fund", cwd=sdk)
    run("npm", "run", "build", cwd=sdk)
    run("npm", "ci", "--no-audit", "--no-fund", cwd=HERE / "js")
    run(
        "node", HERE / "js/node_modules/cypress/bin/cypress", "install", cwd=HERE / "js"
    )


def java():
    output = PREPARED / "java"
    output.mkdir(parents=True, exist_ok=True)
    sdk = ROOT / "client/java"
    run("mvn", "-B", "-ntp", "-Dmaven.test.skip=true", "package", cwd=sdk)
    (shaded,) = (sdk / "target").glob("testlookup-reporter-*-all.jar")
    shutil.copyfile(shaded, output / "sdk.jar")
    plugin = "org.apache.maven.plugins:maven-dependency-plugin:3.9.0:"
    run(
        "mvn",
        "-B",
        "-ntp",
        plugin + "copy-dependencies",
        "-DoutputDirectory=" + str(output / "testng"),
        cwd=HERE / "java",
    )
    for major, version in (("5", "1.14.4"), ("6", "6.1.3")):
        run(
            "mvn",
            "-B",
            "-ntp",
            plugin + "copy",
            "-Dartifact=org.junit.platform:junit-platform-console-standalone:"
            + version,
            "-DoutputDirectory=" + str(output / ("junit" + major)),
            cwd=HERE / "java",
        )
    (junit,) = (output / "junit5").glob("*.jar")
    classpath = os.pathsep.join(
        map(str, (output / "sdk.jar", junit, output / "testng/*"))
    )
    run(
        "javac",
        "--release",
        "17",
        "-cp",
        classpath,
        "-d",
        output,
        HERE / "java/JupiterFixture.java",
        HERE / "java/TestNgFixture.java",
    )


def go():
    PREPARED.mkdir(exist_ok=True)
    binary = PREPARED / ("go-reporter.exe" if os.name == "nt" else "go-reporter")
    run("go", "test", "-c", "-o", binary, cwd=HERE / "go")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "framework",
        choices=("javascript", "java", "go", "all"),
        nargs="?",
        default="all",
    )
    selected = parser.parse_args().framework
    for name, build in (("javascript", javascript), ("go", go), ("java", java)):
        if selected in ("all", name):
            build()
