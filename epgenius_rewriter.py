#!/usr/bin/env python3

import gzip
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# ============================================================
# Configuration
# ============================================================

DATA_DIR = Path("/data")
WORK_DIR = Path("/work")

HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))
REFRESH_MINUTES = max(1, int(os.getenv("REFRESH_MINUTES", "60")))

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://127.0.0.1:8080",
).rstrip("/")

ENCODE_CREDS = os.getenv(
    "URL_ENCODE_CREDENTIALS",
    "true",
).lower() in ("1", "true", "yes", "on")

SERVE_SOURCE_FILES = os.getenv(
    "SERVE_SOURCE_FILES",
    "false",
).lower() in ("1", "true", "yes", "on")

DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))

USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "EPGenius-Rewriter-V2/1.0",
)


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("epgenius-rewriter")


# ============================================================
# Helpers
# ============================================================

def env(name, default=""):
    return os.getenv(name, default).strip()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    )

    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, path)

    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_text(path: Path, text: str):
    atomic_write_bytes(path, text.encode("utf-8"))


def safe_json_write(path: Path, obj):
    atomic_write_text(
        path,
        json.dumps(
            obj,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
    )


def download_bytes(url: str) -> bytes:
    log.info("Downloading: %s", url)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=DOWNLOAD_TIMEOUT,
    ) as response:
        data = response.read()

    log.info(
        "Downloaded %s bytes from %s",
        len(data),
        url,
    )

    return data


def validate_epg(epg_bytes: bytes):
    """
    Validate either XML or gzip-compressed XML.
    Returns the uncompressed XML bytes.
    """

    if epg_bytes[:2] == b"\x1f\x8b":
        xml_bytes = gzip.decompress(epg_bytes)
    else:
        xml_bytes = epg_bytes

    if not xml_bytes.strip():
        raise ValueError("EPG is empty")

    ET.fromstring(xml_bytes)

    return xml_bytes


def gzip_bytes(data: bytes) -> bytes:
    return gzip.compress(
        data,
        compresslevel=6,
        mtime=0,
    )


def encoded_credential(value: str) -> str:
    if ENCODE_CREDS:
        return urllib.parse.quote(value, safe="")
    return value


# ============================================================
# Configuration parsing
# ============================================================

def load_provider(number: int):
    prefix = f"PROVIDER{number}_"

    provider = {
        "number": number,
        "name": env(prefix + "NAME"),
        "output": env(prefix + "OUTPUT"),
        "m3u_url": env(prefix + "M3U_URL"),
        "epg_url": env(prefix + "EPG_URL"),
        "provider_url": env(prefix + "URL"),
        "replace_url": env(
            prefix + "REPLACE_URL",
            "true",
        ).lower() in ("1", "true", "yes", "on"),
        "preserve_path_whitespace": env(
            prefix + "PRESERVE_PATH_WHITESPACE",
            "true",
        ).lower() in ("1", "true", "yes", "on"),
    }

    required = (
        "name",
        "output",
        "m3u_url",
        "epg_url",
        "provider_url",
    )

    missing = [
        key
        for key in required
        if not provider[key]
    ]

    if missing:
        raise ValueError(
            f"Provider {number} missing settings: "
            + ", ".join(missing)
        )

    # --------------------------------------------------------
    # Provider URL pool
    #
    # URL 1 remains PROVIDERn_URL for backward compatibility.
    # Additional URLs use PROVIDERn_URL2, PROVIDERn_URL3, etc.
    # --------------------------------------------------------

    url_count = max(
        1,
        int(env(prefix + "URL_COUNT", "1")),
    )

    provider_urls = {}

    for index in range(1, url_count + 1):
        if index == 1:
            url = env(prefix + "URL")
        else:
            url = env(prefix + f"URL{index}")

        if not url:
            raise ValueError(
                f"Provider {number} URL {index} is missing "
                f"(expected {prefix}{'URL' if index == 1 else f'URL{index}'})"
            )

        provider_urls[index] = url

    provider["url_count"] = url_count
    provider["urls"] = provider_urls

    return provider


def load_output(number: int):
    """
    Legacy OUTPUTn configuration.

    Kept for backward compatibility with the existing six-account
    configuration. New installations should use PROVIDERn_ACCOUNT*
    configuration instead.
    """

    prefix = f"OUTPUT{number}_"

    output = {
        "number": number,
        "provider": int(env(prefix + "PROVIDER", "0")),
        "name": env(prefix + "NAME"),
        "username": env(prefix + "USERNAME"),
        "password": env(prefix + "PASSWORD"),
        "output": env(prefix + "OUTPUT"),
        "url_index": int(env(prefix + "URL", "1")),
    }

    required = (
        "provider",
        "name",
        "username",
        "password",
        "output",
    )

    missing = [
        key
        for key in required
        if not output[key]
    ]

    if missing:
        raise ValueError(
            f"Output {number} missing settings: "
            + ", ".join(missing)
        )

    if output["provider"] not in (1, 2):
        raise ValueError(
            f"Output {number} has invalid provider "
            f"{output['provider']}"
        )

    return output


def load_accounts(provider_number: int):
    """
    Load dynamic accounts for one provider.

    New configuration:

      PROVIDER1_ACCOUNT_COUNT=3

      PROVIDER1_ACCOUNT1_USERNAME=...
      PROVIDER1_ACCOUNT1_PASSWORD=...
      PROVIDER1_ACCOUNT1_URL=1

      PROVIDER1_ACCOUNT2_USERNAME=...
      PROVIDER1_ACCOUNT2_PASSWORD=...
      PROVIDER1_ACCOUNT2_URL=1

    If ACCOUNT_COUNT is not present, fall back to the existing
    OUTPUTn configuration for backward compatibility.
    """

    provider_prefix = f"PROVIDER{provider_number}_"
    count_value = env(provider_prefix + "ACCOUNT_COUNT")

    # --------------------------------------------------------
    # Backward compatibility:
    # If ACCOUNT_COUNT isn't configured, use the existing
    # OUTPUT1-OUTPUT6 configuration.
    # --------------------------------------------------------

    if not count_value:
        return [
            item
            for item in (
                load_output(i)
                for i in range(1, 7)
            )
            if item["provider"] == provider_number
        ]

    try:
        count = int(count_value)
    except ValueError:
        raise ValueError(
            f"{provider_prefix}ACCOUNT_COUNT must be an integer"
        )

    if count < 1:
        raise ValueError(
            f"{provider_prefix}ACCOUNT_COUNT must be at least 1"
        )

    accounts = []

    for account_number in range(1, count + 1):
        prefix = (
            f"{provider_prefix}"
            f"ACCOUNT{account_number}_"
        )

        username = env(prefix + "USERNAME")
        password = env(prefix + "PASSWORD")

        if not username:
            raise ValueError(
                f"Missing {prefix}USERNAME"
            )

        if not password:
            raise ValueError(
                f"Missing {prefix}PASSWORD"
            )

        url_index = int(
            env(prefix + "URL", "1")
        )

        output = env(
            prefix + "OUTPUT",
            f"{env(provider_prefix + 'OUTPUT')}/account{account_number}",
        )

        name = env(
            prefix + "NAME",
            f"Account {account_number}",
        )

        accounts.append(
            {
                "number": account_number,
                "provider": provider_number,
                "name": name,
                "username": username,
                "password": password,
                "output": output,
                "url_index": url_index,
            }
        )

    return accounts


PROVIDERS = {
    1: load_provider(1),
    2: load_provider(2),
}

ACCOUNTS = {
    number: load_accounts(number)
    for number in PROVIDERS
}

# Flattened account list for status/logging compatibility.
OUTPUTS = [
    account
    for accounts in ACCOUNTS.values()
    for account in accounts
]


# ============================================================
# URL rewriting
# ============================================================

PATH_PREFIXES = (
    "/live/",
    "/movie/",
    "/series/",
)


def rewrite_xtream_url(
    url,
    provider,
    username,
    password,
):
    """
    Rewrite Xtream-style URLs while preserving the original
    path whitespace behavior.

    Example:

      http://old/live/olduser/oldpass/123.ts

    becomes:

      http://newuser:newpass@provider/live/newuser/newpass/123.ts

    depending on the provider URL format.
    """

    parsed = urllib.parse.urlsplit(url)

    provider_url = provider["provider_url"]

    if not provider["replace_url"]:
        provider_url = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc
            else provider_url
        )

    provider_parsed = urllib.parse.urlsplit(provider_url)

    scheme = provider_parsed.scheme or parsed.scheme
    netloc = provider_parsed.netloc or parsed.netloc

    if not scheme or not netloc:
        return url

    path = parsed.path

    matched_prefix = None

    for prefix in PATH_PREFIXES:
        if path.startswith(prefix):
            matched_prefix = prefix
            break

    if not matched_prefix:
        return url

    remainder = path[len(matched_prefix):]

    # Existing provider path may have intentional whitespace.
    if provider["preserve_path_whitespace"]:
        leading_ws = len(remainder) - len(remainder.lstrip())
        whitespace = remainder[:leading_ws]
        remainder = remainder[leading_ws:]
    else:
        whitespace = ""

    pieces = remainder.split("/")

    if len(pieces) >= 2:
        pieces[0] = encoded_credential(username)
        pieces[1] = encoded_credential(password)

    new_path = matched_prefix + whitespace + "/".join(pieces)

    return urllib.parse.urlunsplit(
        (
            scheme,
            netloc,
            new_path,
            parsed.query,
            parsed.fragment,
        )
    )


def rewrite_credentials_tag(
    line,
    username,
    password,
    provider,
):
    """
    Rewrite JSON credentials in #EXT-X-CREDENTIALS.
    """

    if not line.startswith("#EXT-X-CREDENTIALS:"):
        return line

    payload = line.split(":", 1)[1].strip()

    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return line

    if isinstance(obj, dict):
        obj["username"] = username
        obj["password"] = password

        provider_url = provider["provider_url"]

        parsed = urllib.parse.urlsplit(provider_url)

        if parsed.hostname:
            obj["host"] = parsed.hostname

        if provider_url:
            obj["url"] = provider_url

    return "#EXT-X-CREDENTIALS:" + json.dumps(
        obj,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def rewrite_m3u(
    source_bytes,
    provider,
    username,
    password,
    epg_url,
):
    """
    Rewrite one provider M3U for one account.
    """

    text = source_bytes.decode(
        "utf-8",
        errors="replace",
    )

    lines = text.splitlines(keepends=True)

    output = []

    rewritten_urls = 0
    entries = 0

    for line in lines:
        newline = ""

        if line.endswith("\r\n"):
            content = line[:-2]
            newline = "\r\n"
        elif line.endswith("\n"):
            content = line[:-1]
            newline = "\n"
        elif line.endswith("\r"):
            content = line[:-1]
            newline = "\r"
        else:
            content = line

        if content.startswith("#EXTINF:"):
            entries += 1

            # Replace url-tvg with the provider's local EPG.
            content = re.sub(
                r'url-tvg="[^"]*"',
                f'url-tvg="{epg_url}"',
                content,
                flags=re.IGNORECASE,
            )

            output.append(content + newline)
            continue

        if content.startswith("#EXT-X-CREDENTIALS:"):
            output.append(
                rewrite_credentials_tag(
                    content,
                    username,
                    password,
                    provider,
                ) + newline
            )
            continue

        stripped = content.strip()

        if stripped and not stripped.startswith("#"):
            try:
                new_url = rewrite_xtream_url(
                    stripped,
                    provider,
                    username,
                    password,
                )

                if new_url != stripped:
                    rewritten_urls += 1

                # Preserve whitespace surrounding the URL line.
                left_ws = content[:len(content) - len(content.lstrip())]
                right_ws = content[len(content.rstrip()):]

                content = (
                    left_ws
                    + new_url
                    + right_ws
                )

            except Exception:
                log.exception(
                    "Failed rewriting URL: %s",
                    stripped,
                )

        output.append(content + newline)

    return (
        "".join(output).encode("utf-8"),
        entries,
        rewritten_urls,
    )


# ============================================================
# Provider refresh
# ============================================================

def refresh_provider(provider):
    number = provider["number"]
    output_name = provider["output"]

    log.info(
        "============================================================"
    )
    log.info(
        "Refreshing provider %s: %s",
        number,
        provider["name"],
    )
    log.info(
        "============================================================"
    )

    provider_dir = DATA_DIR / output_name
    provider_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Download source M3U and EPG ONCE
    # --------------------------------------------------------

    source_m3u = download_bytes(
        provider["m3u_url"]
    )

    source_epg = download_bytes(
        provider["epg_url"]
    )

    # --------------------------------------------------------
    # Validate EPG before touching served files
    # --------------------------------------------------------

    epg_xml = validate_epg(source_epg)
    epg_gz = gzip_bytes(epg_xml)

    # --------------------------------------------------------
    # Write EPG atomically
    # --------------------------------------------------------

    epg_xml_path = provider_dir / "epg.xml"
    epg_gz_path = provider_dir / "epg.xml.gz"

    atomic_write_bytes(
        epg_xml_path,
        epg_xml,
    )

    atomic_write_bytes(
        epg_gz_path,
        epg_gz,
    )

    # --------------------------------------------------------
    # Generate each account from SAME source M3U
    # --------------------------------------------------------

    provider_outputs = ACCOUNTS.get(number, [])

    output_results = []

    for account in provider_outputs:
        url_index = account.get("url_index", 1)

        if url_index not in provider["urls"]:
            raise ValueError(
                f"Account {account['name']} references "
                f"Provider {number} URL {url_index}, "
                f"but only {provider['url_count']} URL(s) are configured"
            )

        # Create an account-specific provider copy.
        # The source M3U and EPG are still downloaded only once.
        account_provider = dict(provider)
        account_provider["provider_url"] = provider["urls"][url_index]

        account_dir = DATA_DIR / account["output"]

        account_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        playlist_url = (
            f"{PUBLIC_BASE_URL}/"
            f"{account['output']}/playlist.m3u"
        )

        local_epg_url = (
            f"{PUBLIC_BASE_URL}/"
            f"{output_name}/epg.xml.gz"
        )

        rewritten_m3u, entries, rewritten_urls = (
            rewrite_m3u(
                source_m3u,
                account_provider,
                account["username"],
                account["password"],
                local_epg_url,
            )
        )

        playlist_path = (
            account_dir / "playlist.m3u"
        )

        atomic_write_bytes(
            playlist_path,
            rewritten_m3u,
        )

        status = {
            "provider": provider["name"],
            "provider_output": output_name,
            "account": account["name"],
            "output": account["output"],
            "generated_at": utc_now(),
            "m3u_entries": entries,
            "stream_urls_rewritten": rewritten_urls,
            "source_m3u_bytes": len(source_m3u),
            "source_epg_bytes": len(source_epg),
            "playlist_url": playlist_url,
            "epg_url": local_epg_url,
            "preserve_path_whitespace": (
                provider["preserve_path_whitespace"]
            ),
            "provider_url_index": url_index,
        }

        safe_json_write(
            account_dir / "status.json",
            status,
        )

        output_results.append(status)

        log.info(
            "%s: %s entries, %s URLs rewritten",
            account["name"],
            entries,
            rewritten_urls,
        )

    # --------------------------------------------------------
    # Provider status
    # --------------------------------------------------------

    provider_status = {
        "provider": provider["name"],
        "output": output_name,
        "generated_at": utc_now(),
        "source_m3u_bytes": len(source_m3u),
        "source_epg_bytes": len(source_epg),
        "epg_xml_bytes": len(epg_xml),
        "epg_gzip_bytes": len(epg_gz),
        "epg_url": (
            f"{PUBLIC_BASE_URL}/"
            f"{output_name}/epg.xml.gz"
        ),
        "outputs": [
            {
                "name": result["account"],
                "output": result["output"],
                "playlist_url": result["playlist_url"],
                "epg_url": result["epg_url"],
                "m3u_entries": result["m3u_entries"],
                "stream_urls_rewritten": (
                    result["stream_urls_rewritten"]
                ),
            }
            for result in output_results
        ],
    }

    safe_json_write(
        provider_dir / "status.json",
        provider_status,
    )

    log.info(
        "Provider %s refresh complete",
        provider["name"],
    )


# ============================================================
# Refresh coordinator
# ============================================================

refresh_lock = threading.Lock()


def refresh_once():
    if not refresh_lock.acquire(blocking=False):
        log.warning(
            "Refresh already running; skipping this cycle"
        )
        return

    try:
        for number in (1, 2):
            provider = PROVIDERS[number]

            try:
                refresh_provider(provider)

            except Exception:
                log.exception(
                    "Provider %s refresh failed. "
                    "Existing output was left untouched where possible.",
                    number,
                )

    finally:
        refresh_lock.release()


def refresh_loop():
    interval = REFRESH_MINUTES * 60

    while True:
        time.sleep(interval)

        try:
            refresh_once()
        except Exception:
            log.exception(
                "Unexpected refresh-loop error"
            )


# ============================================================
# HTTP server
# ============================================================

class Handler(BaseHTTPRequestHandler):

    server_version = "EPGeniusRewriter/2.0"

    def log_message(self, fmt, *args):
        log.info(
            "%s - %s",
            self.client_address[0],
            fmt % args,
        )

    def do_HEAD(self):
        self.serve(send_body=False)

    def do_GET(self):
        self.serve(send_body=True)

    def serve(self, send_body=True):
        path = urllib.parse.urlsplit(
            self.path
        ).path

        if path == "/":
            body = (
                "EPGenius Rewriter V2\n"
                "OK\n"
            ).encode()

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8",
            )
            self.send_header(
                "Content-Length",
                str(len(body)),
            )
            self.end_headers()

            if send_body:
                self.wfile.write(body)

            return

        if path == "/status.json":
            body = json.dumps(
                {
                    "service": "epgenius-rewriter-v2",
                    "generated_at": utc_now(),
                    "providers": {
                        "provider1": PROVIDERS[1]["name"],
                        "provider2": PROVIDERS[2]["name"],
                    },
                },
                indent=2,
            ).encode()

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json",
            )
            self.send_header(
                "Content-Length",
                str(len(body)),
            )
            self.end_headers()

            if send_body:
                self.wfile.write(body)

            return

        if path.startswith("/source/") and not SERVE_SOURCE_FILES:
            self.send_error(
                404,
                "Source files are disabled",
            )
            return

        relative = path.lstrip("/")

        if ".." in Path(relative).parts:
            self.send_error(400, "Invalid path")
            return

        file_path = DATA_DIR / relative

        if not file_path.is_file():
            self.send_error(404, "Not found")
            return

        try:
            body = file_path.read_bytes()

        except OSError:
            self.send_error(500, "Read error")
            return

        if file_path.suffix == ".gz":
            content_type = "application/gzip"
        elif file_path.suffix == ".xml":
            content_type = "application/xml"
        elif file_path.suffix == ".m3u":
            content_type = "audio/x-mpegurl"
        elif file_path.suffix == ".json":
            content_type = "application/json"
        else:
            content_type = "application/octet-stream"

        self.send_response(200)
        self.send_header(
            "Content-Type",
            content_type,
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.send_header(
            "Cache-Control",
            "no-cache",
        )
        self.end_headers()

        if send_body:
            self.wfile.write(body)


# ============================================================
# Main
# ============================================================

def main():
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    WORK_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    log.info(
        "EPGenius Rewriter V2 starting"
    )

    log.info(
        "Providers: %s",
        ", ".join(
            f"{p['number']}={p['name']}"
            for p in PROVIDERS.values()
        ),
    )

    log.info(
        "Accounts: %s",
        ", ".join(
            f"P{o['provider']}/A{o['number']}={o['name']}"
            for o in OUTPUTS
        ),
    )

    # Initial refresh before accepting requests.
    refresh_once()

    thread = threading.Thread(
        target=refresh_loop,
        name="refresh-loop",
        daemon=True,
    )

    thread.start()

    server = ThreadingHTTPServer(
        ("0.0.0.0", HTTP_PORT),
        Handler,
    )

    log.info(
        "HTTP server listening on 0.0.0.0:%s",
        HTTP_PORT,
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        log.info("Stopping")

    finally:
        server.server_close()


if __name__ == "__main__":
    main()
