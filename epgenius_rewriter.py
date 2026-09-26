import gzip
import json
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import gdown
import requests


DATA_DIR = Path("/data")
WORK_DIR = Path("/work")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))
REFRESH_MINUTES = max(1, int(os.getenv("REFRESH_MINUTES", "60")))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
M3U_URL = os.environ["EPGENIUS_M3U_URL"]
EPG_URL = os.environ["EPGENIUS_EPG_URL"]
ENCODE_CREDS = os.getenv("URL_ENCODE_CREDENTIALS", "true").lower() in ("1", "true", "yes", "on")
SERVE_SOURCE_FILES = os.getenv("SERVE_SOURCE_FILES", "false").lower() in ("1", "true", "yes", "on")

session = requests.Session()
session.headers.update({"User-Agent": "EPGenius-Rewriter/2.0"})

PROVIDERS = []
for n in (1, 2):
    prefix = f"PROVIDER{n}_"
    name = os.getenv(prefix + "NAME", f"PROVIDER{n}").strip() or f"PROVIDER{n}"
    output = os.getenv(prefix + "OUTPUT", f"provider{n}").strip() or f"provider{n}"
    output = re.sub(r"[^A-Za-z0-9._-]+", "_", output)
    url = os.getenv(prefix + "URL", "").strip().rstrip("/")
    username = os.getenv(prefix + "USERNAME", "")
    password = os.getenv(prefix + "PASSWORD", "")
    replace_url = os.getenv(prefix + "REPLACE_URL", "true").lower() in ("1", "true", "yes", "on")
    preserve_ws = os.getenv(prefix + "PRESERVE_PATH_WHITESPACE", "true").lower() in ("1", "true", "yes", "on")

    if not url or not username or not password:
        raise RuntimeError(f"{prefix}URL, {prefix}USERNAME and {prefix}PASSWORD are required")

    PROVIDERS.append({
        "id": n,
        "name": name,
        "output": output,
        "url": url,
        "username": username,
        "password": password,
        "replace_url": replace_url,
        "preserve_ws": preserve_ws,
    })


state_lock = threading.Lock()
state = {
    "running": False,
    "last_refresh": None,
    "providers": {},
    "source": {},
    "error": None,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def atomic_write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_text(path: Path, text: str):
    atomic_write_bytes(path, text.encode("utf-8"))


def download_source(url: str, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)

    if "drive.google.com" in url:
        # gdown handles /file/d/.../view and common Drive URLs.
        result = gdown.download(url=url, output=str(destination), quiet=False, fuzzy=True)
        if not result:
            raise RuntimeError(f"Google Drive download failed: {url}")
        return

    with session.get(url, stream=True, timeout=(20, 180)) as r:
        r.raise_for_status()
        with destination.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def encode_credential(value: str) -> str:
    return quote(value, safe="") if ENCODE_CREDS else value


def provider_base_url(provider):
    return provider["url"].rstrip("/")


# Matches Xtream-style stream URLs while preserving the exact whitespace
# immediately following /live/, /movie/, or /series/.
#
# Examples:
#   /live/user/pass/file.ts
#   /live/ user/pass/file.ts
#
# The whitespace is retained when preserve_ws=True.
XTREAM_RE = re.compile(
    r"(?P<prefix>/live/|/movie/|/series/)"
    r"(?P<ws>\s*)"
    r"(?P<user>[^/\s]+)"
    r"/(?P<pass>[^/\s]+)"
    r"/(?P<rest>[^\s\"'<>]+)",
    re.IGNORECASE,
)


def rewrite_stream_url(url: str, provider):
    if not url:
        return url, False

    match = XTREAM_RE.search(url)
    if not match:
        return url, False

    ws = match.group("ws") if provider["preserve_ws"] else ""

    new_user = encode_credential(provider["username"])
    new_pass = encode_credential(provider["password"])

    replacement = (
        match.group("prefix")
        + ws
        + new_user
        + "/"
        + new_pass
        + "/"
        + match.group("rest")
    )

    new_url = url[:match.start()] + replacement + url[match.end():]

    if provider["replace_url"]:
        src = urlsplit(new_url)
        dst = urlsplit(provider_base_url(provider))
        # Preserve path/query/fragment from the original stream URL.
        # The source should normally have an http/https scheme and hostname.
        if dst.scheme and dst.netloc:
            new_url = urlunsplit((
                dst.scheme,
                dst.netloc,
                src.path,
                src.query,
                src.fragment,
            ))

    return new_url, True


def rewrite_credentials_metadata(text: str, provider):
    # EPGenius has used #EXT-X-CREDENTIALS JSON metadata. Update only
    # username/password/dns-like fields when present, without requiring
    # the exact original JSON layout.
    pattern = re.compile(r"(?m)^(#EXT-X-CREDENTIALS:)(.*)$")

    def repl(m):
        prefix, payload = m.group(1), m.group(2)
        try:
            obj = json.loads(payload)
        except Exception:
            return m.group(0)

        def update_obj(item):
            if isinstance(item, dict):
                for key in list(item.keys()):
                    low = key.lower()
                    if low in ("username", "user"):
                        item[key] = provider["username"]
                    elif low in ("password", "pass"):
                        item[key] = provider["password"]
                    elif low in ("dns", "url", "server", "host") and provider["replace_url"]:
                        item[key] = provider_base_url(provider)
                    elif isinstance(item[key], (dict, list)):
                        update_obj(item[key])
            elif isinstance(item, list):
                for item2 in item:
                    update_obj(item2)

        update_obj(obj)
        return prefix + json.dumps(obj, separators=(",", ":"))

    return pattern.sub(repl, text)


def rewrite_m3u(source_text: str, provider):
    lines = source_text.splitlines()
    output = []
    rewritten = 0
    extinf_count = 0

    for line in lines:
        if line.startswith("#EXTINF"):
            extinf_count += 1

        if line.startswith("#EXTM3U"):
            # Replace url-tvg with this provider's local EPG URL.
            epg_url = f"{PUBLIC_BASE_URL}/{provider['output']}/epg.xml.gz"
            if re.search(r'url-tvg\s*=', line, flags=re.IGNORECASE):
                line = re.sub(
                    r'url-tvg\s*=\s*"[^"]*"',
                    f'url-tvg="{epg_url}"',
                    line,
                    flags=re.IGNORECASE,
                )
            else:
                line += f' url-tvg="{epg_url}"'

        if line.startswith("#EXT-X-CREDENTIALS:"):
            line = rewrite_credentials_metadata(line, provider)

        if line and not line.startswith("#"):
            new_line, changed = rewrite_stream_url(line, provider)
            if changed:
                rewritten += 1
            line = new_line

        output.append(line)

    return "\n".join(output) + "\n", extinf_count, rewritten


def make_status(provider, entries, rewritten, source_bytes, epg_bytes):
    return {
        "provider": provider["name"],
        "output": provider["output"],
        "generated_at": now_iso(),
        "m3u_entries": entries,
        "stream_urls_rewritten": rewritten,
        "source_m3u_bytes": source_bytes,
        "source_epg_bytes": epg_bytes,
        "playlist_url": f"{PUBLIC_BASE_URL}/{provider['output']}/playlist.m3u",
        "epg_url": f"{PUBLIC_BASE_URL}/{provider['output']}/epg.xml.gz",
        "preserve_path_whitespace": provider["preserve_ws"],
    }


def refresh_once():
    with state_lock:
        state["running"] = True
        state["error"] = None

    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        source_m3u = WORK_DIR / "source.m3u"
        source_epg = WORK_DIR / "source_epg.xml.gz"

        print("Downloading EPGenius M3U...", flush=True)
        download_source(M3U_URL, source_m3u)
        print(f"Downloaded M3U: {source_m3u.stat().st_size:,} bytes", flush=True)

        print("Downloading EPG...", flush=True)
        download_source(EPG_URL, source_epg)
        print(f"Downloaded EPG: {source_epg.stat().st_size:,} bytes", flush=True)

        source_text = source_m3u.read_text(encoding="utf-8", errors="replace")
        epg_gz = source_epg.read_bytes()

        source_info = {
            "m3u_bytes": len(source_text.encode("utf-8")),
            "epg_gz_bytes": len(epg_gz),
            "downloaded_at": now_iso(),
        }

        if SERVE_SOURCE_FILES:
            atomic_write_bytes(DATA_DIR / "_source.m3u", source_text.encode("utf-8"))
            atomic_write_bytes(DATA_DIR / "_source_epg.xml.gz", epg_gz)

        provider_results = {}

        for provider in PROVIDERS:
            outdir = DATA_DIR / provider["output"]
            outdir.mkdir(parents=True, exist_ok=True)

            rewritten_text, entries, rewritten = rewrite_m3u(source_text, provider)

            # Decompress/recompress only once per provider so each output
            # directory is self-contained for m3u-editor/TiviMate.
            with gzip.GzipFile(fileobj=__import__("io").BytesIO(epg_gz), mode="rb") as gz:
                epg_xml = gz.read()

            compressed_epg = __import__("gzip").compress(epg_xml, compresslevel=6)

            atomic_write_text(outdir / "playlist.m3u", rewritten_text)
            atomic_write_bytes(outdir / "epg.xml.gz", compressed_epg)
            atomic_write_bytes(outdir / "epg.xml", epg_xml)

            status = make_status(
                provider,
                entries,
                rewritten,
                len(source_text.encode("utf-8")),
                len(epg_gz),
            )
            atomic_write_text(
                outdir / "status.json",
                json.dumps(status, indent=2) + "\n",
            )

            provider_results[provider["name"]] = status

            print(
                f"[{provider['name']}] {entries} entries, "
                f"{rewritten}/{entries} stream URLs rewritten",
                flush=True,
            )

        with state_lock:
            state["last_refresh"] = now_iso()
            state["source"] = source_info
            state["providers"] = provider_results

        print("Refresh complete.", flush=True)

    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        print(f"REFRESH ERROR: {exc}", flush=True)

    finally:
        with state_lock:
            state["running"] = False


def refresh_loop():
    while True:
        refresh_once()
        time.sleep(REFRESH_MINUTES * 60)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"HTTP {self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self):
        if self.path == "/status.json":
            with state_lock:
                payload = {
                    **state,
                    "refresh_minutes": REFRESH_MINUTES,
                    "providers_configured": len(PROVIDERS),
                }
            data = (json.dumps(payload, indent=2) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        requested = self.path.split("?", 1)[0].lstrip("/")
        file_path = (DATA_DIR / requested).resolve()

        try:
            file_path.relative_to(DATA_DIR.resolve())
        except ValueError:
            self.send_error(403)
            return

        if not file_path.is_file():
            self.send_error(404)
            return

        data = file_path.read_bytes()

        if file_path.name.endswith(".m3u"):
            content_type = "audio/x-mpegurl"
        elif file_path.name.endswith(".xml.gz"):
            content_type = "application/gzip"
        elif file_path.name.endswith(".xml"):
            content_type = "application/xml"
        elif file_path.name.endswith(".json"):
            content_type = "application/json"
        else:
            content_type = "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    print("=== EPGenius Rewriter - Two Provider Edition ===", flush=True)
    for p in PROVIDERS:
        print(
            f"Provider {p['id']}: {p['name']} -> /{p['output']} "
            f"(preserve whitespace={p['preserve_ws']})",
            flush=True,
        )

    refresh_once()

    threading.Thread(target=refresh_loop, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(f"HTTP server listening on 0.0.0.0:{HTTP_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
