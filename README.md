# EPGenius Rewriter

This container downloads the EPGenius M3U and XMLTV EPG, replaces the
provider credentials in Xtream-style `/live/user/password/...` URLs, updates
the EPG URL in the M3U to a local URL, and serves the result to TiviMate.

## Directory

Suggested VM location:

/srv/docker/epgenius-rewriter/

Files:

- `docker-compose.yml`
- `.env`
- `Dockerfile`
- `requirements.txt`
- `epgenius_rewriter.py`
- `data/`
- `work/`

## Setup

1. Copy `.env.example` to `.env`.
2. Put the EPGenius Google Drive share/download URLs into:
   - `EPGENIUS_M3U_URL`
   - `EPGENIUS_EPG_URL`
3. Put the real IPTV provider URL/username/password into:
   - `PROVIDER_URL`
   - `PROVIDER_USERNAME`
   - `PROVIDER_PASSWORD`
4. Set `PUBLIC_BASE_URL` to the VM address and mapped port.
5. Start:

   docker compose up -d --build

## TiviMate

Playlist:

http://VM-IP:38090/playlist.m3u

EPG:

http://VM-IP:38090/epg.xml.gz

An uncompressed copy is also available:

http://VM-IP:38090/epg.xml

## Security

Do not expose port 38090 directly to the Internet. The generated playlist
contains provider stream URLs and credentials in the URL path, so keep it on
your trusted LAN/VPN or put it behind appropriate access control.

The program intentionally does not print the username/password in logs.

## Important limitation

Google Drive URLs must be downloadable by the container. A public/shared
Drive file works with the included gdown downloader. A private Drive file
requiring a logged-in Google account will need an OAuth/service-account
download method instead.
